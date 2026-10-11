from datetime import timedelta
from unittest.mock import patch
import pytest
from django.utils import timezone
from design_workflow.models import Project
from chat_ai_assistant.contracts import ChatAIError
from .tests import env, executor
from .models import PendingAction

pytestmark=pytest.mark.django_db(transaction=True)


def test_wrong_in_set_ordinal_rejects_before_record_lookup(env):
    second=Project.objects.create(name='Synthetic second',manager=env.owner,status='active')
    state={'resource':'project','ids':[env.project.pk,second.pk],
           'expires_at':(timezone.now()+timedelta(minutes=5)).isoformat()}
    ex=executor(env.owner,state=state,context={'resource':'project','identifier':env.project.pk})
    ex.instruction='Archive the second project.'
    with patch.object(ex,'record') as read_target:
        with pytest.raises(ChatAIError) as caught:
            ex.execute('prepare_change',{'resource':'project','identifier':env.project.pk,'operation':'archive'})
        assert caught.value.code=='CONTEXT_EXPIRED'
        read_target.assert_not_called()
    assert not PendingAction.objects.exists()
    card=ex.execute('prepare_change',{'resource':'project','identifier':second.pk,'operation':'archive'})
    assert card['record_id']==second.pk
    second.refresh_from_db()
    assert not second.archived  # explicit confirmation still required


def test_correct_ordinal_still_rechecks_native_access(env):
    other=Project.objects.create(name='Synthetic restricted',manager=env.outsider,status='active')
    ex=executor(env.owner,state={'resource':'project','ids':[env.project.pk,other.pk],
        'expires_at':(timezone.now()+timedelta(minutes=5)).isoformat()})
    ex.instruction='Archive le deuxième projet.'
    with pytest.raises(ChatAIError) as caught:
        ex.execute('prepare_change',{'resource':'project','identifier':other.pk,'operation':'archive'})
    assert caught.value.code in ('NOT_FOUND','PERMISSION_DENIED')
    assert not PendingAction.objects.exists()

@pytest.mark.parametrize("instruction", [
    "Archive task ID 743.",
    'Update task ID 743 description to "project ID {identifier} is a reference".',
    "Update task ID 743 description to project ID {identifier}.",
])
def test_explicit_other_target_rejects_before_native_record_read(env,instruction):
    ex=executor(env.owner,context={"resource":"project","identifier":env.project.pk})
    ex.instruction=instruction.format(identifier=env.project.pk)
    with patch.object(ex,"record") as read_target:
        with pytest.raises(ChatAIError) as caught:
            ex.execute("prepare_change",{"resource":"project","identifier":env.project.pk,"operation":"archive"})
        assert caught.value.code=="CONTEXT_EXPIRED"
        read_target.assert_not_called()
    assert not PendingAction.objects.exists()

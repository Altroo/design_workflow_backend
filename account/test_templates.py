import pytest
from django.template.loader import render_to_string
from django.utils.translation import override


@pytest.mark.parametrize("language", ["fr", "en"])
@pytest.mark.parametrize(
    "template", ["new_account.html", "new_password.html", "password_reset.html"]
)
def test_email_document_headers_match_language(template, language):
    with override(language):
        document = render_to_string(template)

    assert document.startswith("<!DOCTYPE html>")
    assert f'<html lang="{language}"' in document
    assert '<meta charset="UTF-8">' in document
    assert '<meta name="x-apple-disable-message-reformatting" content="">' in document
    assert '<style type="text/css">' not in document
    # Preserve the compatibility markup used by Outlook.
    assert "o:OfficeDocumentSettings" in document

from rest_framework import serializers


class AssistRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=("translate", "fix_grammar", "professionalize")
    )
    text = serializers.CharField(
        allow_blank=False, trim_whitespace=False, max_length=5000
    )
    source_language = serializers.ChoiceField(
        choices=("auto", "fr", "en"), default="auto"
    )
    target_language = serializers.ChoiceField(choices=("fr", "en"), required=False)
    context = serializers.RegexField(
        r"^[a-z][a-z0-9_]{0,49}$", default="other", max_length=50
    )

    def validate(self, attrs):
        if not attrs["text"].strip():
            raise serializers.ValidationError(
                {"text": "Enter text before using the assistant."}
            )
        if attrs["action"] == "translate" and not attrs.get("target_language"):
            raise serializers.ValidationError(
                {"target_language": "Choose a translation language."}
            )
        if attrs["action"] != "translate":
            attrs.pop("target_language", None)
        return attrs

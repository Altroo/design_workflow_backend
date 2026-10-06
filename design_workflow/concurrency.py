from rest_framework.exceptions import APIException


class EditConflict(APIException):
    status_code = 409
    default_detail = (
        "Cette donnée a été modifiée par une autre personne. "
        "Consultez la dernière version avant d’enregistrer."
    )
    default_code = "edit_conflict"


def check_expected_values(request, instance, serializer_class):
    """Compare only edited fields, under the endpoint's existing row lock."""
    expected = request.data.get("expected_values")
    if expected is None:
        return  # Compatibility for clients that do not yet send edit baselines.
    if not isinstance(expected, dict):
        raise EditConflict()
    current = serializer_class(instance, context={"request": request}).data
    for field, value in expected.items():
        if field not in current or field not in request.data:
            raise EditConflict()
        actual = current[field]
        if isinstance(actual, list) and isinstance(value, list):
            if any(type(item) is not int for item in value):
                raise EditConflict()
            actual, value = sorted(actual), sorted(value)
        if actual != value:
            raise EditConflict()

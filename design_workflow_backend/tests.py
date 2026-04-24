import base64
import binascii
from io import BytesIO
from unittest.mock import patch
import numpy as np
import pytest
from PIL import Image
from django.core.files.base import ContentFile
from rest_framework import serializers
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    PermissionDenied,
    NotFound,
    MethodNotAllowed,
    Throttled,
)
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from .utils import (
    ImageProcessor,
    Base64ImageField,
    api_exception_handler,
    CustomPagination,
)


@pytest.mark.django_db
class TestImageProcessor:
    def test_load_image_from_io(self):
        img = Image.new("RGB", (100, 100), color="red")
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)

        result = ImageProcessor.load_image_from_io(bytes_io)

        assert result is not None
        assert result.shape == (100, 100, 3)

    def test_from_img_to_io(self):
        image_array = np.zeros((100, 100, 3), dtype=np.uint8)
        image_array[:, :] = [255, 0, 0]  # Red image

        result = ImageProcessor.from_img_to_io(image_array, "PNG")

        assert isinstance(result, BytesIO)
        result.seek(0)
        img = Image.open(result)
        assert img.size == (100, 100)
        assert img.format == "PNG"

    def test_data_url_to_uploaded_file_with_header(self):
        img = Image.new("RGB", (10, 10), color="blue")
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)
        encoded = base64.b64encode(bytes_io.read()).decode("utf-8")
        data_url = f"data:image/png;base64,{encoded}"

        result = ImageProcessor.data_url_to_uploaded_file(data_url)

        assert result is not None
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".png")

    def test_data_url_to_uploaded_file_without_header(self):
        img = Image.new("RGB", (10, 10), color="green")
        bytes_io = BytesIO()
        img.save(bytes_io, format="JPEG")
        bytes_io.seek(0)
        encoded = base64.b64encode(bytes_io.read()).decode("utf-8")

        result = ImageProcessor.data_url_to_uploaded_file(encoded)

        assert result is not None
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".jpg")

    def test_data_url_to_uploaded_file_invalid_data(self):
        result = ImageProcessor.data_url_to_uploaded_file("invalid_base64!")
        assert result is None

    def test_data_url_to_uploaded_file_non_string(self):
        result = ImageProcessor.data_url_to_uploaded_file(12345)
        assert result is None

    def test_convert_to_webp(self):
        img = Image.new("RGB", (100, 100), color="blue")
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)
        png_data = bytes_io.read()

        result = ImageProcessor.convert_to_webp(png_data)

        assert isinstance(result, ContentFile)

    def test_convert_to_webp_with_bytesio(self):
        img = Image.new("RGB", (50, 50), color="yellow")
        bytes_io = BytesIO()
        img.save(bytes_io, format="JPEG")
        bytes_io.seek(0)

        result = ImageProcessor.convert_to_webp(bytes_io)

        assert isinstance(result, ContentFile)

    def test_convert_to_webp_too_small(self):
        img = Image.new("RGB", (5, 5), color="red")
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)

        with pytest.raises(ValueError, match="too small"):
            ImageProcessor.convert_to_webp(bytes_io)

    def test_convert_to_webp_non_image(self):
        result = ImageProcessor.convert_to_webp(b"not an image")
        assert result is None

    def test_convert_to_webp_with_transparency(self):
        """Test convert_to_webp with RGBA image (transparency)."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        result = ImageProcessor.convert_to_webp(buf.read())
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".webp")

    def test_convert_to_webp_mode_la(self):
        """Test convert_to_webp with LA mode image (luminance with alpha)."""
        img = Image.new("LA", (50, 50), color=(128, 200))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        result = ImageProcessor.convert_to_webp(buf)
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".webp")

    def test_convert_to_webp_mode_p(self):
        """Test convert_to_webp with P mode image (palette)."""
        img = Image.new("P", (50, 50))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        result = ImageProcessor.convert_to_webp(buf)
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".webp")

    def test_convert_to_webp_mode_l(self):
        """Test convert_to_webp with L mode image (grayscale)."""
        img = Image.new("L", (50, 50), color=128)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        result = ImageProcessor.convert_to_webp(buf)
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".webp")

    def test_convert_to_webp_mode_cmyk(self):
        """Test convert_to_webp with CMYK mode image (converts to RGB)."""
        img = Image.new("CMYK", (50, 50), color=(100, 50, 0, 0))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        result = ImageProcessor.convert_to_webp(buf)
        assert isinstance(result, ContentFile)
        assert result.name.endswith(".webp")

    def test_resize_with_blurred_background_landscape(self):
        """Test resize with landscape image (wider than tall)."""
        image = np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8)
        result = ImageProcessor.resize_with_blurred_background(image, target_size=300)
        assert result.shape == (300, 300, 3)

    def test_resize_with_blurred_background_portrait(self):
        """Test resize with portrait image (taller than wide)."""
        image = np.random.randint(0, 255, (200, 100, 3), dtype=np.uint8)
        result = ImageProcessor.resize_with_blurred_background(image, target_size=300)
        assert result.shape == (300, 300, 3)

    def test_resize_with_blurred_background_square(self):
        """Test resize with square image."""
        image = np.random.randint(0, 255, (150, 150, 3), dtype=np.uint8)
        result = ImageProcessor.resize_with_blurred_background(image, target_size=300)
        assert result.shape == (300, 300, 3)


class TestBase64ImageField:
    def test_valid_base64_png_string(self):
        img = Image.new("RGB", (10, 10), color="red")
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)
        b64 = base64.b64encode(bytes_io.read()).decode("utf-8")

        field = Base64ImageField()
        # Base64ImageField converts the base64 string to a ContentFile
        result = field.to_internal_value(b64)
        assert hasattr(result, "name")
        assert result.name.endswith(".png")

    def test_data_url_format_base64(self):
        img = Image.new("RGB", (10, 10), color="blue")
        bytes_io = BytesIO()
        img.save(bytes_io, format="JPEG")
        bytes_io.seek(0)
        b64 = base64.b64encode(bytes_io.read()).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"

        field = Base64ImageField()
        result = field.to_internal_value(data_url)
        assert hasattr(result, "name")

    def test_get_file_extension_jpeg_returns_jpg(self):
        img = Image.new("RGB", (10, 10))
        bytes_io = BytesIO()
        img.save(bytes_io, format="JPEG")
        bytes_io.seek(0)
        decoded = bytes_io.read()

        ext = Base64ImageField.get_file_extension("test", decoded)
        assert ext == "jpg"

    def test_get_file_extension_png_returns_png(self):
        img = Image.new("RGB", (10, 10))
        bytes_io = BytesIO()
        img.save(bytes_io, format="PNG")
        bytes_io.seek(0)
        decoded = bytes_io.read()

        ext = Base64ImageField.get_file_extension("test", decoded)
        assert ext == "png"

    def test_get_file_extension_invalid_returns_jpg(self):
        ext = Base64ImageField.get_file_extension("test", b"not-an-image")
        assert ext == "jpg"

    def test_to_internal_value_with_invalid_base64(self):
        """Test to_internal_value raises on invalid base64 string."""
        field = Base64ImageField()
        with pytest.raises(binascii.Error):
            field.to_internal_value("invalid_base64_string!!!")

    def test_to_internal_value_with_file_object(self):
        """Test to_internal_value with a file object (delegates to parent)."""
        field = Base64ImageField()
        img = Image.new("RGB", (10, 10), color="yellow")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        content_file = ContentFile(buf.read(), name="test.png")

        with patch.object(
            serializers.ImageField, "to_internal_value", return_value=content_file
        ):
            result = field.to_internal_value(content_file)
            assert result == content_file


class TestApiExceptionHandler:
    factory = APIRequestFactory()

    def _make_context(self, method="get", path="/test/"):
        request = self.factory.get(path)
        return {"request": Request(request), "view": None}

    def test_not_found_returns_formatted_response(self):
        exc = NotFound("Resource not found")
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 404
        assert response.data["status_code"] == 404
        assert "Aucune correspondance" in response.data["message"]

    def test_permission_denied_returns_formatted_response(self):
        exc = PermissionDenied("Access denied")
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 403
        assert response.data["status_code"] == 403
        assert (
            "refusé" in response.data["message"].lower()
            or "Accès" in response.data["message"]
        )

    def test_authentication_failed_returns_formatted_response(self):
        exc = AuthenticationFailed("Not authenticated")
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 401
        assert response.data["status_code"] == 401

    def test_validation_error_returns_formatted_response(self):
        exc = ValidationError({"field": ["This field is required."]})
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 400
        assert response.data["status_code"] == 400

    def test_method_not_allowed_returns_formatted_response(self):
        exc = MethodNotAllowed("POST")
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 405

    def test_non_api_exception_returns_none(self):
        exc = Exception("Unknown error")
        context = self._make_context()
        response = api_exception_handler(exc, context)
        assert response is None

    def test_500_server_error(self):
        """Test api_exception_handler with 500 status."""
        exc = APIException()
        exc.status_code = 500
        context = self._make_context()
        response = api_exception_handler(exc, context)
        assert response is not None
        assert response.status_code == 500
        assert response.data["status_code"] == 500

    def test_throttled_exception_with_french_message(self):
        exc = Throttled(wait=5)
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert response.status_code == 429

    def test_throttled_exception_singular_second(self):
        exc = Throttled(wait=1)
        context = self._make_context()
        response = api_exception_handler(exc, context)

        assert response is not None
        assert "seconde" in str(exc.detail)


@pytest.mark.django_db
class TestCustomPagination:
    def test_default_page_size(self):
        paginator = CustomPagination()
        assert paginator.page_size == 10

    def test_max_page_size(self):
        paginator = CustomPagination()
        assert paginator.max_page_size == 100

    def test_page_size_query_param(self):
        paginator = CustomPagination()
        assert paginator.page_size_query_param == "page_size"

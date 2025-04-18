import asyncio
import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from googletrans import Translator


async def context_specific_translation(text):
    async with Translator() as translator:
        if (await translator.detect(text)) == 'en':
            return text
        return (await translator.translate(text, dest='en')).text


def translate_text(text):
    return asyncio.run(context_specific_translation(text))


def convert_image_to_base64(image) -> str:
    """Converts image to base64 string"""

    return base64.b64encode(image.read()).decode()


def convert_str_to_image(image_data: str):
    """Converts base 64 string to django image"""

    decoded_data = base64.b64decode(image_data.encode())

    file = SimpleUploadedFile.from_dict(
        {
            "filename": "logo",
            "content": decoded_data,
            "content-type": "'image/jpeg'",
        }
    )

    return file

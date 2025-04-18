from django import template
import math

register = template.Library()


@register.filter
def duration_format(milliseconds):
    """Convert milliseconds to MM:SS format"""
    if not milliseconds:
        return "0:00"

    milliseconds = milliseconds.total_seconds() * 1000

    # Convert milliseconds to seconds
    seconds = math.floor(milliseconds / 1000)

    # Calculate minutes and remaining seconds
    minutes = math.floor(seconds / 60)
    remaining_seconds = seconds % 60

    # Format as MM:SS
    return f"{minutes}:{remaining_seconds:02d}"


@register.filter
def multiply(value, arg):
    return value * arg

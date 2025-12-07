"""
Custom exception handlers for the MeloMatch API.
"""
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that provides consistent error responses.

    Response format:
    {
        "error": {
            "code": "error_code",
            "message": "Human readable message",
            "details": {...}  # Optional additional details
        }
    }
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)

    if response is not None:
        # Log the exception
        view = context.get('view', None)
        logger.warning(
            f"API Exception in {view.__class__.__name__ if view else 'Unknown'}: "
            f"{exc.__class__.__name__} - {str(exc)}"
        )

        # Standardize error response format
        error_data = {
            'error': {
                'code': _get_error_code(exc),
                'message': _get_error_message(exc, response),
                'status_code': response.status_code,
            }
        }

        # Add details for validation errors
        if hasattr(exc, 'detail') and isinstance(exc.detail, dict):
            error_data['error']['details'] = exc.detail

        response.data = error_data
    else:
        # Handle unexpected exceptions
        logger.exception(f"Unhandled exception: {exc}")
        response = Response(
            {
                'error': {
                    'code': 'server_error',
                    'message': 'An unexpected error occurred. Please try again later.',
                    'status_code': status.HTTP_500_INTERNAL_SERVER_ERROR,
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return response


def _get_error_code(exc):
    """Map exception to a machine-readable error code."""
    from rest_framework.exceptions import (
        AuthenticationFailed,
        NotAuthenticated,
        NotFound,
        PermissionDenied,
        Throttled,
        ValidationError,
    )

    error_codes = {
        ValidationError: 'validation_error',
        NotAuthenticated: 'not_authenticated',
        AuthenticationFailed: 'authentication_failed',
        PermissionDenied: 'permission_denied',
        NotFound: 'not_found',
        Throttled: 'rate_limit_exceeded',
    }

    return error_codes.get(type(exc), 'api_error')


def _get_error_message(exc, response):
    """Get a human-readable error message."""
    if hasattr(exc, 'detail'):
        if isinstance(exc.detail, str):
            return exc.detail
        elif isinstance(exc.detail, list):
            return exc.detail[0] if exc.detail else 'An error occurred.'

    # Default messages based on status code
    default_messages = {
        400: 'Invalid request data.',
        401: 'Authentication required.',
        403: 'You do not have permission to perform this action.',
        404: 'The requested resource was not found.',
        429: 'Too many requests. Please try again later.',
        500: 'An internal server error occurred.',
    }

    return default_messages.get(response.status_code, 'An error occurred.')

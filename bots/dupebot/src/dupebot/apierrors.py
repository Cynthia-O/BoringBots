"""Plain-language messages for errors from a model call, shared by the CLI and the browser app."""

import anthropic

from dupebot.analyze import AnalysisError

NO_KEY = (
    "No Anthropic API key found. Copy .env.example to .env in the BoringBots folder, "
    "add your key, and run DupeBot from that folder."
)

# Errors a model call is expected to raise; anything else is a bug and is re-raised.
EXPECTED = (anthropic.CredentialsError, anthropic.APIError, AnalysisError)


def describe(error: Exception, model: str) -> str:
    if isinstance(error, anthropic.CredentialsError):
        return NO_KEY
    if isinstance(error, anthropic.AuthenticationError):
        return f"The API key was rejected. {NO_KEY}"
    if isinstance(error, anthropic.NotFoundError):
        return f"Model not found: {model}"
    if isinstance(error, anthropic.RateLimitError):
        return "Rate limited by the API. Wait a minute and try again."
    if isinstance(error, anthropic.APIStatusError):
        return f"The API returned an error ({error.status_code}): {error.message}"
    if isinstance(error, anthropic.APIConnectionError):
        return "Could not reach the API. Check your internet connection."
    if isinstance(error, AnalysisError):
        return f"Analysis failed: {error}"
    return f"{type(error).__name__}: {error}"

#  Copyright (c) ZenML GmbH 2020. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.
"""Analytics code for ZenML"""

from enum import Enum
from typing import Any, Callable, Dict, Optional, Union

from zenml import __version__
from zenml.constants import IS_DEBUG_ENV, SEGMENT_KEY_DEV, SEGMENT_KEY_PROD
from zenml.environment import Environment
from zenml.logger import get_logger

logger = get_logger(__name__)


class AnalyticsEvent(str, Enum):
    # Pipelines
    RUN_PIPELINE = "Pipeline run"
    GET_PIPELINES = "Pipelines fetched"
    GET_PIPELINE = "Pipeline fetched"

    # Repo
    INITIALIZE_REPO = "ZenML initialized"

    # Profile
    INITIALIZED_PROFILE = "Profile initialized"

    # Components
    REGISTERED_STACK_COMPONENT = "Stack component registered"
    UPDATED_STACK_COMPONENT = "Stack component updated"

    # Stack
    REGISTERED_STACK = "Stack registered"
    SET_STACK = "Stack set"
    UPDATED_STACK = "Stack updated"

    # Analytics opt in and out
    OPT_IN_ANALYTICS = "Analytics opt-in"
    OPT_OUT_ANALYTICS = "Analytics opt-out"

    # Examples
    RUN_EXAMPLE = "Example run"
    PULL_EXAMPLE = "Example pull"

    # Integrations
    INSTALL_INTEGRATION = "Integration installed"

    # Test event
    EVENT_TEST = "Test event"


def get_segment_key() -> str:
    """Get key for authorizing to Segment backend.

    Returns:
        Segment key as a string.
    """
    if IS_DEBUG_ENV:
        return SEGMENT_KEY_DEV
    else:
        return SEGMENT_KEY_PROD


def get_environment() -> str:
    """Returns a string representing the execution environment of the pipeline.
    Currently, one of `docker`, `paperspace`, 'colab', or `native`"""
    if Environment.in_docker():
        return "docker"
    elif Environment.in_google_colab():
        return "colab"
    elif Environment.in_paperspace_gradient():
        return "paperspace"
    elif Environment.in_notebook():
        return "notebook"
    else:
        return "native"


def track_event(
    event: Union[str, AnalyticsEvent],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Track segment event if user opted-in.

    Args:
        event: Name of event to track in segment.
        metadata: Dict of metadata to track.

    Returns:
        True if event is sent successfully, False is not.
    """
    try:
        import analytics

        from zenml.config.global_config import GlobalConfiguration

        if analytics.write_key is None:
            analytics.write_key = get_segment_key()

        assert (
            analytics.write_key is not None
        ), "Analytics key not set but trying to make telemetry call."

        # Set this to 1 to avoid backoff loop
        analytics.max_retries = 1

        gc = GlobalConfiguration()
        if isinstance(event, AnalyticsEvent):
            event = event.value

        logger.debug(
            f"Attempting analytics: User: {gc.user_id}, "
            f"Event: {event},"
            f"Metadata: {metadata}"
        )

        if not gc.analytics_opt_in and event not in {
            AnalyticsEvent.OPT_OUT_ANALYTICS,
            AnalyticsEvent.OPT_IN_ANALYTICS,
        }:
            return False

        if metadata is None:
            metadata = {}

        # add basics
        metadata.update(Environment.get_system_info())
        metadata.update(
            {
                "environment": get_environment(),
                "python_version": Environment.python_version(),
                "version": __version__,
            }
        )

        analytics.track(str(gc.user_id), event, metadata)
        logger.debug(
            f"Analytics sent: User: {gc.user_id}, Event: {event}, Metadata: "
            f"{metadata}"
        )
        return True
    except Exception as e:
        # We should never fail main thread
        logger.debug(f"Analytics failed due to: {e}")
        return False


def parametrized(
    dec: Callable[..., Callable[..., Any]]
) -> Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]]:
    """This is a meta-decorator, that is, a decorator for decorators.
    As a decorator is a function, it actually works as a regular decorator
    with arguments:"""

    def layer(
        *args: Any, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Internal layer"""

        def repl(f: Callable[..., Any]) -> Callable[..., Any]:
            """Internal repl"""
            return dec(f, *args, **kwargs)

        return repl

    return layer


@parametrized
def track(
    func: Callable[..., Any],
    event: Optional[Union[str, AnalyticsEvent]] = None,
) -> Callable[..., Any]:
    """Decorator to track event.

    Args:
        func: Function that is decorated.
        event: Event string to stamp with.
    """
    # Need to redefine the name for the event here in order for mypy
    # to recognize it's not an optional string anymore
    # TODO [ENG-168]: open bug ticket and link here
    event_name = event or func.__name__  # default to name of function
    metadata: Dict[str, Any] = {}

    def inner_func(*args: Any, **kwargs: Any) -> Any:
        """Inner decorator function."""
        track_event(event_name, metadata=metadata)
        result = func(*args, **kwargs)
        return result

    return inner_func

#  Copyright (c) ZenML GmbH 2021. All Rights Reserved.
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
"""Utility functions to start/stop daemon processes.

This is only implemented for UNIX systems and therefore doesn't work on
Windows. Based on
https://www.jejik.com/articles/2007/02/a_simple_unix_linux_daemon_in_python/
"""

import atexit
import os
import signal
import sys
import types
from typing import Any, Callable, Optional, TypeVar, cast

import psutil

from zenml.logger import get_logger

logger = get_logger(__name__)

# TODO [ENG-235]: Investigate supporting Windows if Windows can run Kubeflow.


F = TypeVar("F", bound=Callable[..., Any])


def daemonize(
    pid_file: str,
    log_file: Optional[str] = None,
    working_directory: str = "/",
) -> Callable[[F], F]:
    """Decorator that executes the decorated function as a daemon process.

    Use this decorator to easily transform any function into a daemon
    process.

    Example:

    ```python
    import time
    from zenml.utils.daemonizer import daemonize


    @daemonize(log_file='/tmp/daemon.log', pid_file='/tmp/daemon.pid')
    def sleeping_daemon(period: int) -> None:
        print(f"I'm a daemon! I will sleep for {period} seconds.")
        time.sleep(period)
        print("Done sleeping, flying away.")

    sleeping_daemon(period=30)

    print("I'm the daemon's parent!.")
    time.sleep(10) # just to prove that the daemon is running in parallel
    ```

    Args:
        _func: decorated function
        pid_file: an optional file where the PID of the daemon process will
            be stored.
        log_file: file where stdout and stderr are redirected for the daemon
            process. If not supplied, the daemon will be silenced (i.e. have
            its stdout/stderr redirected to /dev/null).
        working_directory: working directory for the daemon process,
            defaults to the root directory.
    Returns:
        Decorated function that, when called, will detach from the current
        process and continue executing in the background, as a daemon
        process.
    """

    def inner_decorator(_func: F) -> F:
        def daemon(*args: Any, **kwargs: Any) -> None:
            """Standard daemonization of a process."""
            # flake8: noqa: C901
            if sys.platform == "win32":
                logger.error(
                    "Daemon functionality is currently not supported on Windows."
                )
            else:
                run_as_daemon(
                    _func,
                    log_file=log_file,
                    pid_file=pid_file,
                    working_directory=working_directory,
                    *args,
                    **kwargs,
                )

        return cast(F, daemon)

    return inner_decorator


# flake8: noqa: C901
if sys.platform == "win32":
    logger.warning(
        "Daemon functionality is currently not supported on Windows."
    )
else:

    CHILD_PROCESS_WAIT_TIMEOUT = 5

    def terminate_children() -> None:
        """Terminate all processes that are children of the currently running
        process.
        """
        pid = os.getpid()
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            # could not find parent process id
            return
        children = parent.children(recursive=False)

        for p in children:
            p.terminate()
        _, alive = psutil.wait_procs(
            children, timeout=CHILD_PROCESS_WAIT_TIMEOUT
        )
        for p in alive:
            p.kill()
        _, alive = psutil.wait_procs(
            children, timeout=CHILD_PROCESS_WAIT_TIMEOUT
        )

    def run_as_daemon(
        daemon_function: F,
        *args: Any,
        pid_file: str,
        log_file: Optional[str] = None,
        working_directory: str = "/",
        **kwargs: Any,
    ) -> None:
        """Runs a function as a daemon process.

        Args:
            daemon_function: The function to run as a daemon.
            pid_file: Path to file in which to store the PID of the daemon
                process.
            log_file: Optional file to which the daemons stdout/stderr will be
                redirected to.
            working_directory: Working directory for the daemon process,
                defaults to the root directory.
            args: Positional arguments to pass to the daemon function.
            kwargs: Keyword arguments to pass to the daemon function.
        Raises:
            FileExistsError: If the PID file already exists.
        """
        # convert to absolute path as we will change working directory later
        if pid_file:
            pid_file = os.path.abspath(pid_file)
        if log_file:
            log_file = os.path.abspath(log_file)

        # check if PID file exists
        if pid_file and os.path.exists(pid_file):
            raise FileExistsError(
                f"The PID file '{pid_file}' already exists, either the daemon "
                f"process is already running or something went wrong."
            )

        # first fork
        try:
            pid = os.fork()
            if pid > 0:
                # this is the process that called `run_as_daemon` so we
                # simply return so it can keep running
                return
        except OSError as e:
            logger.error("Unable to fork (error code: %d)", e.errno)
            sys.exit(1)

        # decouple from parent environment
        os.chdir(working_directory)
        os.setsid()
        os.umask(0o22)

        # second fork
        try:
            pid = os.fork()
            if pid > 0:
                # this is the parent of the future daemon process, kill it
                # so the daemon gets adopted by the init process
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"Unable to fork (error code: {e.errno})")
            sys.exit(1)

        # redirect standard file descriptors to devnull (or the given logfile)
        devnull = "/dev/null"
        if hasattr(os, "devnull"):
            devnull = os.devnull

        devnull_fd = os.open(devnull, os.O_RDWR)
        log_fd = (
            os.open(log_file, os.O_CREAT | os.O_RDWR | os.O_APPEND)
            if log_file
            else None
        )
        out_fd = log_fd or devnull_fd

        os.dup2(devnull_fd, sys.stdin.fileno())
        os.dup2(out_fd, sys.stdout.fileno())
        os.dup2(out_fd, sys.stderr.fileno())

        if pid_file:
            # write the PID file
            with open(pid_file, "w+") as f:
                f.write(f"{os.getpid()}\n")

        # register actions in case this process exits/gets killed
        def cleanup() -> None:
            """Daemon cleanup."""
            terminate_children()
            if pid_file and os.path.exists(pid_file):
                os.remove(pid_file)

        def sighndl(signum: int, frame: Optional[types.FrameType]) -> None:
            """Daemon signal handler."""
            cleanup()

        signal.signal(signal.SIGTERM, sighndl)
        signal.signal(signal.SIGINT, sighndl)
        atexit.register(cleanup)

        # finally run the actual daemon code
        daemon_function(*args, **kwargs)
        sys.exit(0)

    def stop_daemon(pid_file: str) -> None:
        """Stops a daemon process.

        Args:
            pid_file: Path to file containing the PID of the daemon process to
                kill.
        """
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
        except (IOError, FileNotFoundError):
            logger.warning("Daemon PID file '%s' does not exist.", pid_file)
            return

        if psutil.pid_exists(pid):
            process = psutil.Process(pid)
            process.terminate()
        else:
            logger.warning("PID from '%s' does not exist.", pid_file)

    def get_daemon_pid_if_running(pid_file: str) -> Optional[int]:
        """Read and return the PID value from a PID file if the daemon process
        tracked by the PID file is running.

        Args:
            pid_file: Path to file containing the PID of the daemon
                process to check.
        Returns:
            The PID of the daemon process if it is running, otherwise None.
        """
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
        except (IOError, FileNotFoundError):
            return None

        if not pid or not psutil.pid_exists(pid):
            return None

        return pid

    def check_if_daemon_is_running(pid_file: str) -> bool:
        """Checks whether a daemon process indicated by the PID file is running.

        Args:
            pid_file: Path to file containing the PID of the daemon
                process to check.
        """
        return get_daemon_pid_if_running(pid_file) is not None

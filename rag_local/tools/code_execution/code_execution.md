# Code Execution Tool Specification

The Code Execution Tool executes python code blocks in a secure, sandboxed container environments using Docker.

## Components
- **SandboxResult**: A dataclass tracking the status and output of a container script run.
- **run_python_in_docker**: Spins up a read-only Docker container using the `python:3.11-slim` image, mounts the temporary script file as well as the repository directory, and returns the result (stdout, stderr, exit code).

## Configuration
- Docker must be installed and running on the host machine.
- Execution is strictly isolated: network access is disabled (`--network none`), memory is capped to 512MB, CPU is capped to 1 core, and PIDs are limited to 64.

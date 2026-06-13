# Shiba Inu Image Extractor Script

## Description
A lightweight utility that scans a source directory, resolves required model paths, and extracts image assets (or related data) into a specified output directory. It is intended for automated extraction within the Rag local tooling pipeline.

## Usage
```bash
python extract_image.py <source_dir> <output_dir>
```

- **`<source_dir>`** – Path to the directory containing source files (e.g., `./shiba_images`).  
- **`<output_dir>`** – Destination folder where extracted images will be placed; the script creates it if necessary.

## Dependencies
- **Python 3.11+** (image based on `python:3.11-slim`).  
- Runtime module **`requests`** – not pre‑installed in the environment; the script will raise an error if this module is missing.

## Docker Command
```bash
docker run --rm -v $(pwd):/workspace python:3.11-slim python /workspace/docker_scripts/extract_image.py <source_dir> <output_dir>
```

- `--rm` – removes the container after it finishes.  
- `-v $(pwd):/workspace` – maps the host’s current directory into `/workspace` inside the container, allowing the script to read source files and write outputs.  
- The script writes its output to the specified `<output_dir>` within the container.

## Fallback / Error Handling
If Docker is unavailable on the host, `run_python_in_docker` returns a `SandboxResult` with `success=False` and an error message indicating the missing tool. **No fallback to host execution is performed**; the script does not automatically run the code outside Docker.

## Related Documentation URLs
- **Code execution & sandboxing**: <https://agent-sandbox.sigs.k8s.io/docs/use-cases/code-execution/>  
  - Details how the script uses a secure Python sandbox. Mentions that when Docker is unavailable, the sandbox reports an error rather than falling back to host execution.

---  

*This documentation summarizes the purpose, invocation, and error‑handling behavior of the Shiba Inu image extractor.*
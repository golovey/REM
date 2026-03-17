#!/usr/bin/env python3
"""
Remote Environment Manager - Run commands, parse output, and reuse parsed values
Browser-based interface using Flask
"""

from flask import Flask, render_template, request, jsonify, Response
import subprocess
import re
import os
import pty
import webbrowser
import threading
import queue
import time
import yaml
import select
import fcntl
import termios
import signal

app = Flask(__name__)

# Strip ANSI/VT100 terminal escape sequences (cursor movement, colors, etc.)
# These are produced by interactive programs running in a PTY but make no
# sense in a plain-text web terminal.
ANSI_ESCAPE = re.compile(
    r'\x1b'
    r'(?:'
    r'\][^\x07\x1b]*(?:\x07|\x1b\\)?'  # OSC sequences first  e.g. \x1b]0;title\x07  \x1b]11;?
    r'|\[[0-?]*[ -/]*[@-~]'             # CSI sequences        e.g. \x1b[?25l  \x1b[1;32m
    r'|[@-Z\\-_]'                       # two-byte sequences   e.g. \x1bM
    r')'
)


def collapse_cr_overwrites(text: str) -> str:
    """Simulate terminal \\r behaviour: keep only text after last carriage return."""
    parts = text.split('\r')
    return parts[-1] if parts else text

# Open /dev/tty once at startup so command output is always echoed to the
# controlling terminal regardless of how Flask redirects sys.stdout.
try:
    _tty_fd = os.open('/dev/tty', os.O_WRONLY)
except OSError:
    _tty_fd = None

# Store terminal output and parsed modules in memory
terminal_history = []
parsed_modules = []

# Track the currently running process for interactive input
current_process = None
current_master_fd = None
process_lock = threading.Lock()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/run_command', methods=['POST'])
def run_command():
    """Execute a shell command and return output"""
    data = request.json
    command = data.get('command', '').strip()

    if not command:
        return jsonify({'error': 'No command provided'}), 400

    try:
        # Run command and capture output
        # Use bash login shell to load PATH and environment
        result = subprocess.run(
            ['/bin/bash', '-l', '-c', command],
            capture_output=True,
            text=True,
            timeout=30
        )

        # Build output
        output = result.stdout
        if result.stderr:
            output += f"\n--- STDERR ---\n{result.stderr}"

        # Store in history
        terminal_history.append({
            'command': command,
            'output': output,
            'exit_code': result.returncode
        })

        return jsonify({
            'success': True,
            'command': command,
            'output': output,
            'exit_code': result.returncode
        })

    except subprocess.TimeoutExpired:
        error_msg = "Error: Command timed out (30s limit)"
        terminal_history.append({
            'command': command,
            'output': error_msg,
            'exit_code': -1
        })
        return jsonify({'error': error_msg}), 500
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        terminal_history.append({
            'command': command,
            'output': error_msg,
            'exit_code': -1
        })
        return jsonify({'error': error_msg}), 500


@app.route('/run_command_stream', methods=['POST'])
def run_command_stream():
    """Execute a shell command and stream output in real-time"""
    data = request.json
    command = data.get('command', '').strip()

    if not command:
        return jsonify({'error': 'No command provided'}), 400

    def generate():
        """Generator function to stream command output via a PTY.

        Using a PTY (pseudo-terminal) is essential for interactive commands:
        programs detect they are connected to a real terminal and flush output
        immediately (including prompts that have no trailing newline), rather
        than buffering until the pipe fills up.
        """
        global current_process, current_master_fd
        output_buffer = []
        master_fd = None

        try:
            master_fd, slave_fd = pty.openpty()

            def _set_ctty():
                # Create a new session so this process has no controlling terminal,
                # then assign the PTY slave (fd 0) as the controlling terminal.
                # This ensures programs like sudo write their password prompt to
                # the PTY (and thus to the browser UI) rather than to the terminal
                # window where Flask was started.
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                ['/bin/bash', '-l', '-c', command],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=_set_ctty,
            )
            # slave_fd is owned by the child process now; close our copy
            os.close(slave_fd)

            with process_lock:
                current_process = process
                current_master_fd = master_fd

            line_buffer = b""

            while True:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                except (ValueError, OSError):
                    break  # master_fd was closed

                if ready:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break  # process exited and PTY was closed
                    if not chunk:
                        break

                    if _tty_fd is not None:
                        os.write(_tty_fd, chunk)

                    decoded = chunk.decode('utf-8', errors='replace')
                    output_buffer.append(decoded)
                    line_buffer += chunk

                    # Yield each complete line immediately
                    while b'\n' in line_buffer:
                        line, line_buffer = line_buffer.split(b'\n', 1)
                        line_text = collapse_cr_overwrites(
                            ANSI_ESCAPE.sub('', line.decode('utf-8', errors='replace').rstrip('\r'))
                        )
                        yield f"data: {line_text}\n\n"

                else:
                    # 50 ms timeout with no data — flush partial line (interactive prompt)
                    if line_buffer:
                        partial = collapse_cr_overwrites(
                            ANSI_ESCAPE.sub('', line_buffer.decode('utf-8', errors='replace').rstrip('\r'))
                        )
                        yield f"data: [PARTIAL]{partial}\n\n"
                        line_buffer = b""

                    if process.poll() is not None:
                        break

            # Flush anything still in the buffer
            if line_buffer:
                partial = collapse_cr_overwrites(
                    ANSI_ESCAPE.sub('', line_buffer.decode('utf-8', errors='replace').rstrip('\r'))
                )
                yield f"data: [PARTIAL]{partial}\n\n"

            process.wait()
            exit_code = process.returncode
            yield f"data: [COMPLETE:{exit_code}]\n\n"

            terminal_history.append({
                'command': command,
                'output': ''.join(output_buffer),
                'exit_code': exit_code
            })

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            yield f"data: {error_msg}\n\n"
            yield f"data: [COMPLETE:-1]\n\n"

            terminal_history.append({
                'command': command,
                'output': error_msg,
                'exit_code': -1
            })

        finally:
            with process_lock:
                current_process = None
                current_master_fd = None
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    return Response(generate(), mimetype='text/event-stream')


@app.route('/parse_modules', methods=['POST'])
def parse_modules():
    """Parse terminal history to extract module names from status table"""
    global parsed_modules

    # Get all terminal output
    all_output = '\n'.join([entry['output'] for entry in terminal_history])

    unique_modules = []
    seen = set()
    found_section = False
    parsing_table = False

    # Known database names to exclude (they appear in the right column)
    database_names = {
        'kafka-controller', 'keycloak', 'opensearch', 'postgres',
        'redis-master', 'mongo', 'mongodb', 'mysql', 'localstack'
    }

    lines = all_output.split('\n')

    for line in lines:
        # First, find the "Deployed Modules & Databases" section
        if not found_section:
            if 'Deployed Modules & Databases' in line or 'Deployed Modules' in line:
                found_section = True
            continue

        # After finding the section, look for the table header
        if found_section and not parsing_table:
            if 'NAME' in line and 'READY' in line:
                parsing_table = True
                continue

        # Stop parsing if we hit an empty line or section break
        if parsing_table and not line.strip():
            parsing_table = False
            continue

        # Parse module names from table rows (left column only)
        if parsing_table and line.strip():
            # Check if the line starts with meaningful content (module name position)
            # Module names start around column 2-4, not at column 50+
            stripped_line = line.lstrip()
            if not stripped_line or len(line) - len(stripped_line) > 50:
                # Line is too indented, likely only has database info on right side
                continue

            # Extract the first word (module name) from the left column
            parts = line.split()
            if len(parts) >= 1:
                module_name = parts[0].strip()

                # Filter out non-module lines and database names
                if (module_name and
                    module_name.lower() not in database_names and
                    not module_name.startswith('�') and
                    not module_name.startswith('📦') and
                    not module_name.startswith('🗄️') and
                    module_name not in ['Modules', 'Databases', 'NAME', 'READY', 'UP-TO-DATE', 'AVAILABLE', 'AGE'] and
                    not any(char.isdigit() for char in module_name) and  # No numbers in name
                    module_name not in seen and
                    len(module_name) > 2):
                    unique_modules.append(module_name)
                    seen.add(module_name)

    parsed_modules = unique_modules

    return jsonify({
        'success': True,
        'modules': unique_modules,
        'count': len(unique_modules)
    })


@app.route('/get_modules', methods=['GET'])
def get_modules():
    """Get current parsed modules"""
    return jsonify({'modules': parsed_modules})


@app.route('/get_k8s_namespace', methods=['GET'])
def get_k8s_namespace():
    """Read the current kubectl namespace from the active context."""
    try:
        result = subprocess.run(
            ['kubectl', 'config', 'view', '--minify',
             '--output=jsonpath={.contexts[0].context.namespace}'],
            capture_output=True, text=True, timeout=5
        )
        namespace = result.stdout.strip()
        if namespace:
            return jsonify({'namespace': namespace})
        return jsonify({'error': 'No namespace set in current context'}), 404
    except FileNotFoundError:
        return jsonify({'error': 'kubectl not found'}), 404
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'kubectl timed out'}), 500


@app.route('/send_input', methods=['POST'])
def send_input():
    """Send input (keystrokes) to the currently running process via the PTY master fd"""
    global current_process, current_master_fd
    data = request.json
    input_data = data.get('input', '')

    with process_lock:
        if current_process is None or current_process.poll() is not None:
            return jsonify({'error': 'No running process'}), 400
        if current_master_fd is None:
            return jsonify({'error': 'No PTY available'}), 400
        try:
            os.write(current_master_fd, input_data.encode('utf-8'))
            return jsonify({'success': True})
        except OSError as e:
            return jsonify({'error': str(e)}), 500


@app.route('/kill_command', methods=['POST'])
def kill_command():
    """Send Ctrl+C (SIGINT) to the currently running process via the PTY"""
    global current_process, current_master_fd
    with process_lock:
        if current_process is None or current_process.poll() is not None:
            return jsonify({'error': 'No running process'}), 400
        try:
            if current_master_fd is not None:
                os.write(current_master_fd, b'\x03')  # Ctrl+C through PTY
            else:
                current_process.send_signal(signal.SIGINT)
            return jsonify({'success': True})
        except OSError as e:
            return jsonify({'error': str(e)}), 500


@app.route('/clear_terminal', methods=['POST'])
def clear_terminal():
    """Clear terminal history"""
    global terminal_history
    terminal_history = []
    return jsonify({'success': True})


@app.route('/get_yaml_groups', methods=['GET'])
def get_yaml_groups():
    """Parse gong-modules-base.yaml and return groups with their modules"""
    yaml_path = os.path.expanduser('~/develop/code/gong-build-commons/dev/gong-module-runner/conf/gong-modules-base.yaml')

    try:
        if not os.path.exists(yaml_path):
            return jsonify({
                'error': f'YAML file not found at: {yaml_path}'
            }), 404

        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)

        # Build groups dictionary by inverting module->groups relationship
        groups = {}

        if 'subsystems' in data:
            for subsystem in data['subsystems']:
                subsystem_name = subsystem.get('name', 'unknown')
                modules = subsystem.get('modules', [])

                for module in modules:
                    module_name = module.get('name')
                    module_groups = module.get('groups', [])

                    # Add module to each group it belongs to
                    for group_name in module_groups:
                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(module_name)

        return jsonify({
            'success': True,
            'groups': groups,
            'total_groups': len(groups)
        })

    except yaml.YAMLError as e:
        return jsonify({'error': f'YAML parsing error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Error reading file: {str(e)}'}), 500


@app.route('/get_running_pods', methods=['GET'])
def get_running_pods():
    """Get deduplicated module names from running pods, grouped by subsystem from YAML.

    Uses the pod's 'app' label as the canonical module name.
    Falls back to stripping trailing k8s hash suffixes from the pod name.
    Modules not found in the YAML are placed in an 'Other' group.
    """
    yaml_path = os.path.expanduser('~/develop/code/gong-build-commons/dev/gong-module-runner/conf/gong-modules-base.yaml')

    # Build module -> subsystem lookup from YAML (best-effort)
    module_to_subsystem = {}
    try:
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r') as f:
                yaml_data = yaml.safe_load(f)
            if 'subsystems' in yaml_data:
                for subsystem in yaml_data['subsystems']:
                    subsystem_name = subsystem.get('name', 'unknown')
                    for module in subsystem.get('modules', []):
                        module_name = module.get('name')
                        if module_name and module_name != 'placeholder' and module.get('web_port') != 11111:
                            module_to_subsystem[module_name] = subsystem_name
    except Exception:
        pass  # YAML unavailable — all pods go into "Other"

    try:
        result = subprocess.run(
            ['kubectl', 'get', 'pods', '--field-selector=status.phase=Running',
             '-o', r'jsonpath={range .items[*]}{.metadata.labels.app}{"\t"}{.metadata.name}{"\n"}{end}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return jsonify({'error': result.stderr.strip() or 'kubectl failed'}), 500

        seen = set()
        grouped = {}  # subsystem_name -> [module_names]

        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            app_label = parts[0].strip() if parts else ''
            pod_name = parts[1].strip() if len(parts) > 1 else ''

            if app_label:
                name = app_label
            else:
                # Strip trailing k8s suffixes: <name>-<replicaset-hash>-<pod-hash>
                name = re.sub(r'-[a-z0-9]{5,10}-[a-z0-9]{5}$', '', pod_name)
                # Strip StatefulSet ordinal: <name>-<number>
                name = re.sub(r'-\d+$', '', name)

            if name and name not in seen:
                seen.add(name)
                subsystem = module_to_subsystem.get(name, 'Other')
                grouped.setdefault(subsystem, []).append(name)

        # Sort modules within each group; sort groups alphabetically, "Other" last
        for subsystem in grouped:
            grouped[subsystem].sort()

        sorted_grouped = {
            k: grouped[k]
            for k in sorted(grouped, key=lambda x: (x == 'Other', x))
        }

        total = sum(len(v) for v in sorted_grouped.values())
        return jsonify({'success': True, 'grouped_pods': sorted_grouped, 'count': total})
    except FileNotFoundError:
        return jsonify({'error': 'kubectl not found'}), 404
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'kubectl timed out'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/stream_pod_logs')
def stream_pod_logs():
    pod = request.args.get('pod', '').strip()
    container = request.args.get('container', '').strip()
    tail = request.args.get('tail', '200')

    if not pod:
        return jsonify({'error': 'No pod specified'}), 400

    # Sidecars to skip when auto-selecting the main container
    SIDECAR_CONTAINERS = {'linkerd-proxy', 'linkerd-init', 'istio-proxy', 'istio-init'}

    # Resolve the app-label name to an actual pod name and its container list.
    # The running pods list uses the 'app' label value, not the raw pod name.
    actual_pod = ''
    main_container = container  # honour explicit caller override
    try:
        lookup = subprocess.run(
            ['kubectl', 'get', 'pods', '-l', f'app={pod}',
             '-o', r'jsonpath={.items[0].metadata.name}{"\t"}'
                   r'{range .items[0].spec.containers[*]}{.name}{"\n"}{end}'],
            capture_output=True, text=True, timeout=5
        )
        lines = lookup.stdout.strip().splitlines()
        if lines:
            first_line_parts = lines[0].split('\t', 1)
            actual_pod = first_line_parts[0].strip()
            if not main_container and len(first_line_parts) > 1:
                # containers are on lines[0] (after tab) and lines[1:]
                container_names = [first_line_parts[1].strip()] + [l.strip() for l in lines[1:] if l.strip()]
                non_sidecar = [c for c in container_names if c not in SIDECAR_CONTAINERS]
                main_container = non_sidecar[0] if non_sidecar else (container_names[0] if container_names else '')
    except Exception:
        pass

    target = actual_pod if actual_pod else pod
    cmd = ['kubectl', 'logs', '-f', f'--tail={tail}', target]
    if main_container:
        cmd += ['-c', main_container]

    def generate():
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True
            )
            for line in process.stdout:
                yield f"data: {line.rstrip()}\n\n"
            process.wait()
            yield f"data: [LOG_END:{process.returncode}]\n\n"
        except FileNotFoundError:
            yield "data: [ERROR]kubectl not found\n\n"
        except Exception as e:
            yield f"data: [ERROR]{str(e)}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/get_yaml_subsystems', methods=['GET'])
def get_yaml_subsystems():
    """Parse gong-modules-base.yaml and return subsystems with their modules"""
    yaml_path = os.path.expanduser('~/develop/code/gong-build-commons/dev/gong-module-runner/conf/gong-modules-base.yaml')

    try:
        if not os.path.exists(yaml_path):
            return jsonify({
                'error': f'YAML file not found at: {yaml_path}'
            }), 404

        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)

        # Build subsystems dictionary from YAML structure
        subsystems = {}

        if 'subsystems' in data:
            for subsystem in data['subsystems']:
                subsystem_name = subsystem.get('name', 'unknown')
                modules = subsystem.get('modules', [])

                # Extract module names, filtering out placeholders
                module_names = []
                for module in modules:
                    module_name = module.get('name')
                    web_port = module.get('web_port')

                    # Skip placeholder modules
                    if module_name and module_name != 'placeholder' and web_port != 11111:
                        module_names.append(module_name)

                # Only add subsystem if it has real modules
                if module_names:
                    subsystems[subsystem_name] = module_names

        return jsonify({
            'success': True,
            'subsystems': subsystems,
            'total_subsystems': len(subsystems)
        })

    except yaml.YAMLError as e:
        return jsonify({'error': f'YAML parsing error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Error reading file: {str(e)}'}), 500


def open_browser():
    """Open browser after a short delay to ensure server is ready"""
    import time
    time.sleep(1.5)  # Wait for Flask server to start
    webbrowser.open('http://localhost:5000')


if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)

    print("=" * 60)
    print("Remote Environment Manager")
    print("=" * 60)
    print("\nStarting server...")
    print("\n📱 Opening browser at http://localhost:5000")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 60 + "\n")

    # Open browser only once (not on Flask reloader restart)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(debug=True, port=5000)

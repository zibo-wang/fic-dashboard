import ast
import os
import re
import shlex  # For safer parsing of command lines in bash scripts
from pathlib import Path

import kuzu

# --- Configuration ---
DB_PATH = "automation_graph_v2.kuzu"
# PROJECT_DIRECTORIES should be the root(s) of your projects/packages
# e.g., if you have my_project/src, my_project/lib, my_project/scripts,
# and imports happen relative to my_project, then add my_project path.
PROJECT_DIRECTORIES = [
    # "/path/to/your/project_root1",
    # "/path/to/your/project_root2",
]

# JOB_DEFINITIONS: command -> relative_path_of_entry_script (py or sh)
# The relative path is from one of the PROJECT_DIRECTORIES.
JOB_DEFINITIONS = {
    # "python main_job1.py --config prod": "src/project1/main_job1.py",
    # "./run_etl.sh --daily": "scripts/etl/run_etl.sh",
    # "airflow_trigger_dag my_dag": "dags/my_dag_definition.py" # Example
}
# --- End Configuration ---


# --- Helper: AST Visitor for Imports ---
class ImportCollector(ast.NodeVisitor):
    def __init__(self, current_file_path_abs_str):
        self.imports = set()
        self.current_file_path_abs = Path(current_file_path_abs_str).resolve()
        self.current_file_dir = self.current_file_path_abs.parent

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.add(alias.name)  # e.g., 'os', 'my_package.my_module'
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module_name_str = ""
        if node.module:  # from foo import bar -> node.module is 'foo'
            module_name_str = node.module

        if node.level > 0:  # Relative import
            # For "from . import foo", module_name_str is "", level is 1. 'foo' is in node.names
            # For "from .bar import foo", module_name_str is "bar", level is 1.
            # We want to capture the module path that needs resolution.
            if (
                module_name_str
            ):  # e.g. from .bar import X  OR from ..bar import X
                self.imports.add(
                    f"RELATIVE_MODULE:{'.' * node.level}{module_name_str}"
                )
            else:  # e.g. from . import X
                # Here, X (in node.names) is the entity being imported from the package level.
                # If X is a module (X.py or X/__init__.py), we treat it as such.
                for alias in node.names:
                    # This implies trying to resolve alias.name in the directory specified by node.level
                    self.imports.add(
                        f"RELATIVE_NAME:{'.' * node.level}:{alias.name}"
                    )
        elif (
            module_name_str
        ):  # Absolute import: from package.module import name
            self.imports.add(module_name_str)
        self.generic_visit(node)


# --- Kuzu Functions ---
def create_kuzu_schema(conn):
    print("Creating Kuzu schema...")
    conn.execute("""
        CREATE NODE TABLE IF NOT EXISTS Job (
            command STRING,
            type STRING, 
            entry_script_path STRING, /* Abs path to the .py or .sh file */
            PRIMARY KEY (command)
        )
    """)
    conn.execute("""
        CREATE NODE TABLE IF NOT EXISTS PythonScript (
            path STRING, /* Abs path to the .py file */
            name STRING, /* Filename of the .py file */
            PRIMARY KEY (path)
        )
    """)
    conn.execute("""
        CREATE NODE TABLE IF NOT EXISTS ExternalModule (
            name STRING, /* Name of the external module, e.g., 'requests' */
            PRIMARY KEY (name)
        )
    """)

    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS HAS_MAIN_PYTHON_SCRIPT (FROM Job TO PythonScript)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS EXECUTES_PYTHON_SCRIPT (FROM Job TO PythonScript)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS IMPORTS_SCRIPT (FROM PythonScript TO PythonScript)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS IMPORTS_EXTERNAL (FROM PythonScript TO ExternalModule)"
    )
    print("Schema created/verified.")


def insert_job(conn, command, job_type, entry_script_path):
    conn.execute(
        "MERGE (j:Job {command: $command}) ON CREATE SET j.type = $type, j.entry_script_path = $path",
        {"command": command, "type": job_type, "path": entry_script_path},
    )


def insert_python_script(conn, path_str, name_str):
    conn.execute(
        "MERGE (ps:PythonScript {path: $path}) ON CREATE SET ps.name = $name",
        {"path": path_str, "name": name_str},
    )


def insert_external_module(conn, name_str):
    conn.execute("MERGE (em:ExternalModule {name: $name})", {"name": name_str})


def create_job_to_py_script_relationship(
    conn, job_command, py_script_path_str, rel_type
):
    query = f"""
        MATCH (j:Job {{command: $job_command}}), (ps:PythonScript {{path: $py_script_path}})
        MERGE (j)-[:{rel_type}]->(ps)
    """
    conn.execute(
        query,
        {"job_command": job_command, "py_script_path": py_script_path_str},
    )


def create_script_import_relationship(
    conn, source_py_script_path_str, target_py_script_path_str
):
    query = """
        MATCH (s_ps:PythonScript {path: $source_path}), (t_ps:PythonScript {path: $target_path})
        MERGE (s_ps)-[:IMPORTS_SCRIPT]->(t_ps)
    """
    conn.execute(
        query,
        {
            "source_path": source_py_script_path_str,
            "target_path": target_py_script_path_str,
        },
    )


def create_external_import_relationship(
    conn, source_py_script_path_str, target_external_module_name_str
):
    query = """
        MATCH (s_ps:PythonScript {path: $source_path}), (em:ExternalModule {name: $target_name})
        MERGE (s_ps)-[:IMPORTS_EXTERNAL]->(em)
    """
    conn.execute(
        query,
        {
            "source_path": source_py_script_path_str,
            "target_name": target_external_module_name_str,
        },
    )


# --- Core Logic ---
def find_all_project_files(directories, extensions=(".py",)):
    found_files = {}
    project_roots_found = set()  # Store Path objects for resolved roots
    for proj_dir_str in directories:
        proj_dir = Path(proj_dir_str).resolve()
        if not proj_dir.is_dir():
            print(
                f"Warning: Project directory '{proj_dir_str}' not found or not a directory."
            )
            continue
        project_roots_found.add(proj_dir)
        for root, _, files in os.walk(proj_dir):
            for file in files:
                if file.endswith(extensions):
                    abs_path = (Path(root) / file).resolve()
                    # Relative path calculation remains tricky if multiple project_dirs overlap
                    # For simplicity, we'll just store absolute, name can be derived.
                    found_files[str(abs_path)] = (
                        file  # Store abs_path -> filename
                    )
    return found_files, project_roots_found


def parse_python_invocations_from_bash(
    bash_script_abs_path_str,
    all_project_py_files_abs_set,
    project_roots_resolved,
):
    """
    Parses a bash script to find Python script invocations.
    Returns a set of absolute paths to Python scripts found within our project.
    """
    py_invocations_abs_paths = set()
    bash_script_path = Path(bash_script_abs_path_str)
    bash_script_dir = bash_script_path.parent

    try:
        with open(
            bash_script_path, "r", encoding="utf-8", errors="ignore"
        ) as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  Error reading bash script {bash_script_path.name}: {e}")
        return py_invocations_abs_paths

    current_cwd = (
        bash_script_dir  # Approximation: CWD starts as script's directory
    )

    for line_num, line_content in enumerate(lines):
        line = line_content.strip()
        if not line or line.startswith("#"):
            continue

        # Very basic 'cd' handling: affects subsequent path resolutions on this simplified model
        # This is extremely naive and won't handle complex cd logic or variables.
        if line.startswith("cd "):
            try:
                cd_path_str = shlex.split(line)[1]
                cd_path = Path(cd_path_str)
                if cd_path.is_absolute():
                    current_cwd = cd_path.resolve()
                else:
                    current_cwd = (current_cwd / cd_path).resolve()
                # print(f"    Bash CWD approx changed to: {current_cwd} in {bash_script_path.name}")
            except Exception:
                # print(f"    Bash CWD: Failed to parse cd args in {bash_script_path.name}: {line}")
                pass  # Ignore cd parse errors, keep previous CWD
            continue  # cd line processed

        try:
            args = shlex.split(line)
        except ValueError:  # Unmatched quotes etc.
            args = line.split()  # Fallback

        if not args:
            continue

        potential_py_script_arg = None
        cmd = args[0]

        if cmd == "python" or cmd == "python3":
            for arg in args[1:]:
                if arg.endswith(".py") and not arg.startswith("-"):
                    potential_py_script_arg = arg
                    break
        elif cmd.endswith(".py") and (
            Path(cmd).name == cmd or "/" in cmd or cmd.startswith(".")
        ):  # e.g. ./script.py or path/to/script.py
            potential_py_script_arg = cmd

        if potential_py_script_arg:
            resolved_script_abs = None
            # Try resolving path:
            # 1. As absolute
            # 2. Relative to current_cwd (approximated from bash script's dir and naive cd)
            # 3. Relative to project roots (if not a clear relative path like ./ or ../)

            # Check if potential_py_script_arg is already absolute
            if Path(potential_py_script_arg).is_absolute():
                resolved_script_abs = str(
                    Path(potential_py_script_arg).resolve()
                )
            else:
                # Relative to current_cwd (approximated)
                path_rel_to_cwd = (
                    current_cwd / potential_py_script_arg
                ).resolve()
                if str(path_rel_to_cwd) in all_project_py_files_abs_set:
                    resolved_script_abs = str(path_rel_to_cwd)
                # Fallback: if not found via CWD, and not like "./script.py", try from project roots
                # This is for cases where scripts might be on a PATH set up by the bash script implicitly.
                elif not potential_py_script_arg.startswith(("./", "../")):
                    for proj_root in project_roots_resolved:
                        path_rel_to_proj_root = (
                            proj_root / potential_py_script_arg
                        ).resolve()
                        if (
                            str(path_rel_to_proj_root)
                            in all_project_py_files_abs_set
                        ):
                            resolved_script_abs = str(path_rel_to_proj_root)
                            break

            if (
                resolved_script_abs
                and resolved_script_abs in all_project_py_files_abs_set
            ):
                py_invocations_abs_paths.add(resolved_script_abs)
            # else:
            #     print(f"    Bash: Py script '{potential_py_script_arg}' (from '{bash_script_path.name}') not found in project or unresolvable. CWD approx: {current_cwd}")

    return py_invocations_abs_paths


def resolve_import_path(
    import_name_raw,
    importing_file_abs_path_str,
    all_project_py_files_abs_set,
    project_roots_resolved,
):
    importing_file_path = Path(importing_file_abs_path_str).resolve()
    importing_dir = importing_file_path.parent

    def _find_module_file(base_dir, module_parts):
        # Try module_parts.py
        current_path = base_dir
        for part in module_parts[:-1]:
            current_path /= part

        # Check for <base_dir>/<module_parts_joined_by_slash>.py
        py_file_path = current_path / (module_parts[-1] + ".py")
        if str(py_file_path.resolve()) in all_project_py_files_abs_set:
            return str(py_file_path.resolve())

        # Check for <base_dir>/<module_parts_joined_by_slash>/__init__.py
        init_file_path = base_dir
        for part in module_parts:
            init_file_path /= part
        init_file_path /= "__init__.py"
        if str(init_file_path.resolve()) in all_project_py_files_abs_set:
            return str(init_file_path.resolve())
        return None

    # 1. Handle Relative Imports
    if import_name_raw.startswith(
        "RELATIVE_MODULE:"
    ):  # e.g., from .sub import foo -> RELATIVE_MODULE:..sub
        import_str = import_name_raw[len("RELATIVE_MODULE:") :]
        level = import_str.count(".")
        module_path_str = import_str.lstrip(".")

        base_path_for_relative = importing_dir
        for _ in range(level - 1):  # Go up for each dot beyond the first one
            base_path_for_relative = base_path_for_relative.parent

        module_parts = module_path_str.split(".")
        resolved_path = _find_module_file(base_path_for_relative, module_parts)
        if resolved_path:
            return resolved_path, True  # (path_str, is_custom)

    elif import_name_raw.startswith(
        "RELATIVE_NAME:"
    ):  # e.g., from . import foo -> RELATIVE_NAME:.:foo
        import_str = import_name_raw[len("RELATIVE_NAME:") :]
        level_str, name_to_resolve = import_str.split(":", 1)
        level = level_str.count(".")

        base_path_for_relative = importing_dir
        for _ in range(level - 1):
            base_path_for_relative = base_path_for_relative.parent

        # name_to_resolve is the direct module name we're looking for (e.g., "foo" for "foo.py")
        resolved_path = _find_module_file(
            base_path_for_relative, [name_to_resolve]
        )
        if resolved_path:
            return resolved_path, True

    # 2. Handle Absolute Imports
    else:  # e.g. my_package.my_module
        module_parts = import_name_raw.split(".")
        for proj_root in (
            project_roots_resolved
        ):  # project_roots_resolved contains Path objects
            resolved_path = _find_module_file(proj_root, module_parts)
            if resolved_path:
                return resolved_path, True

    # If not found as custom, treat as external or unresolvable
    final_name_for_external = import_name_raw  # Default
    if import_name_raw.startswith("RELATIVE_MODULE:"):
        final_name_for_external = import_name_raw.split(":")[-1].lstrip(".")
    elif import_name_raw.startswith("RELATIVE_NAME:"):
        final_name_for_external = import_name_raw.split(":")[-1]

    # print(f"    Import '{import_name_raw}' (from {importing_file_path.name}) resolved as external/unknown: '{final_name_for_external}'")
    return final_name_for_external, False


def create_dummy_env():
    print("Creating dummy files for testing...")
    dummy_root = Path("test_proj_v2_env").resolve()
    dummy_root.mkdir(parents=True, exist_ok=True)

    (dummy_root / "src").mkdir(exist_ok=True)
    (dummy_root / "lib").mkdir(exist_ok=True)
    (dummy_root / "scripts").mkdir(exist_ok=True)
    (dummy_root / "src" / "common").mkdir(exist_ok=True)

    with open(dummy_root / "src" / "main_job.py", "w") as f:
        f.write("import os\n")
        f.write(
            "from ..lib import utils # Relative import to sibling package\n"
        )
        f.write("import common.helpers  # Absolute import within src package\n")
        f.write(
            "from . import sub_module # Relative import within same package\n"
        )
        f.write("print('Main job running')\n")

    with open(dummy_root / "src" / "sub_module.py", "w") as f:
        f.write("print('Src sub_module reporting!')\n")

    with open(dummy_root / "lib" / "utils.py", "w") as f:
        f.write("import requests\n")
        f.write(
            "from . import nested_util # Relative import inside lib package\n"
        )
        f.write("def do_util_stuff(): print('Utils stuff')\n")

    with open(dummy_root / "lib" / "nested_util.py", "w") as f:
        f.write("print('Lib nested_util reporting!')\n")

    with open(dummy_root / "src" / "common" / "helpers.py", "w") as f:
        f.write("def help_me(): print('Helping!')\n")

    with open(dummy_root / "scripts" / "run_all.sh", "w") as f:
        f.write("#!/bin/bash\n")
        f.write("echo 'Starting tasks from bash'\n")
        f.write(
            "python ../src/main_job.py --mode=daily\n"
        )  # Path relative to scripts/
        f.write("cd ../lib \n")  # Change CWD
        f.write("python utils.py \n")  # Path relative to new CWD (lib/)
        f.write("echo 'Bash script finished'\n")
    os.chmod(dummy_root / "scripts" / "run_all.sh", 0o755)

    with open(dummy_root / "scripts" / "direct_py_task.py", "w") as f:
        f.write("import sys\n")
        # This import will likely fail to resolve if 'scripts' is not structured as a package
        # that can see 'lib'. To make `from ..lib import utils` work from `scripts/direct_py_task.py`,
        # `scripts` and `lib` would need to be sub-packages of `test_proj_v2_env`.
        f.write(
            "# from ..lib import utils # This requires test_proj_v2_env to be on PYTHONPATH\n"
        )
        f.write("print('Direct Python task from scripts reporting')\n")

    global PROJECT_DIRECTORIES, JOB_DEFINITIONS
    PROJECT_DIRECTORIES = [str(dummy_root)]  # The root of the project structure
    JOB_DEFINITIONS = {
        "python src/main_job.py --init": "src/main_job.py",  # Relative to dummy_root
        "./scripts/run_all.sh batch": "scripts/run_all.sh",  # Relative to dummy_root
        "python scripts/direct_py_task.py": "scripts/direct_py_task.py",  # Relative to dummy_root
    }
    print(f"Dummy environment created at: {dummy_root}")
    print(
        "Review JOB_DEFINITIONS and PROJECT_DIRECTORIES for your actual projects."
    )


def main():
    if not PROJECT_DIRECTORIES or not JOB_DEFINITIONS:
        create_dummy_env()

    db = kuzu.Database(DB_PATH)
    conn = kuzu.Connection(db)
    create_kuzu_schema(conn)

    all_py_files_map, py_project_roots = find_all_project_files(
        PROJECT_DIRECTORIES, extensions=(".py",)
    )
    all_sh_files_map, sh_project_roots = find_all_project_files(
        PROJECT_DIRECTORIES, extensions=(".sh", ".bash")
    )
    project_roots_resolved = py_project_roots.union(
        sh_project_roots
    )  # Set of Path objects
    all_project_py_files_abs_set = set(
        all_py_files_map.keys()
    )  # Set of abs path strings

    print(f"Found {len(all_project_py_files_abs_set)} Python files.")
    print(f"Found {len(all_sh_files_map)} Shell scripts.")

    # Python scripts whose imports need to be parsed. Start with job entry points.
    python_scripts_to_parse_queue = set()

    # 1. Process JOB_DEFINITIONS to create Job nodes and identify initial Python scripts
    for command, entry_script_rel_path_str in JOB_DEFINITIONS.items():
        entry_script_abs_path = None
        # Resolve entry_script_rel_path_str against project roots
        for root_path in project_roots_resolved:  # root_path is a Path object
            potential_path = (root_path / entry_script_rel_path_str).resolve()
            if potential_path.exists():
                entry_script_abs_path = str(potential_path)
                break

        if not entry_script_abs_path:
            print(
                f"Warning: Entry script '{entry_script_rel_path_str}' for job '{command}' not found."
            )
            continue

        job_type = None
        if entry_script_abs_path.endswith((".py", ".pyw")):
            job_type = "python"
            insert_job(conn, command, job_type, entry_script_abs_path)
            py_script_name = Path(entry_script_abs_path).name
            insert_python_script(conn, entry_script_abs_path, py_script_name)
            create_job_to_py_script_relationship(
                conn, command, entry_script_abs_path, "HAS_MAIN_PYTHON_SCRIPT"
            )
            python_scripts_to_parse_queue.add(entry_script_abs_path)
            print(
                f"Registered Python Job: '{command}' -> '{entry_script_abs_path}'"
            )

        elif entry_script_abs_path.endswith((".sh", ".bash")):
            job_type = "bash"
            insert_job(conn, command, job_type, entry_script_abs_path)
            print(
                f"Registered Bash Job: '{command}' -> '{entry_script_abs_path}'"
            )

            invoked_py_scripts = parse_python_invocations_from_bash(
                entry_script_abs_path,
                all_project_py_files_abs_set,
                project_roots_resolved,
            )
            if not invoked_py_scripts:
                print(
                    f"  No project Python scripts found or resolved in bash script: {Path(entry_script_abs_path).name}"
                )

            for py_abs_path_str in invoked_py_scripts:
                # Already validated that py_abs_path_str is in all_project_py_files_abs_set by parse_python_invocations_from_bash
                py_script_name = Path(py_abs_path_str).name
                insert_python_script(conn, py_abs_path_str, py_script_name)
                create_job_to_py_script_relationship(
                    conn, command, py_abs_path_str, "EXECUTES_PYTHON_SCRIPT"
                )
                python_scripts_to_parse_queue.add(py_abs_path_str)
                print(
                    f"  Bash job '{Path(entry_script_abs_path).name}' executes: '{py_abs_path_str}'"
                )
        else:
            print(
                f"Warning: Unknown job type for entry script '{entry_script_abs_path}' (command: '{command}')"
            )

    # 2. Parse all unique Python files found in project directories for their imports
    # This ensures all modules are processed, not just direct entry points.
    # We use all_project_py_files_abs_set which contains all .py files discovered.
    processed_for_ast = set()

    # Create a combined queue: entry points first, then all other python files.
    # This isn't strictly necessary for correctness due to MERGE, but can be logical.
    parse_agenda = list(
        python_scripts_to_parse_queue
    )  # Start with identified entry points
    for py_file_abs in all_project_py_files_abs_set:
        if py_file_abs not in parse_agenda:
            parse_agenda.append(py_file_abs)

    for py_abs_path_str in parse_agenda:
        if py_abs_path_str in processed_for_ast:
            continue
        processed_for_ast.add(py_abs_path_str)

        # print(f"\nProcessing Python script for imports: {py_abs_path_str}")
        py_script_name = Path(py_abs_path_str).name
        insert_python_script(
            conn, py_abs_path_str, py_script_name
        )  # Ensure node exists

        try:
            with open(
                py_abs_path_str, "r", encoding="utf-8", errors="ignore"
            ) as f:
                content = f.read()
            tree = ast.parse(content, filename=py_abs_path_str)

            collector = ImportCollector(py_abs_path_str)
            collector.visit(tree)

            if collector.imports:
                print(
                    f"  Imports in '{py_script_name}': {', '.join(sorted(list(collector.imports)))}"
                )

            for imp_name_raw in collector.imports:
                resolved_target_id, is_custom_script = resolve_import_path(
                    imp_name_raw,
                    py_abs_path_str,
                    all_project_py_files_abs_set,
                    project_roots_resolved,
                )

                if is_custom_script:
                    target_script_path = (
                        resolved_target_id  # This is an abs path string
                    )
                    target_script_name = Path(target_script_path).name
                    insert_python_script(
                        conn, target_script_path, target_script_name
                    )
                    create_script_import_relationship(
                        conn, py_abs_path_str, target_script_path
                    )
                    # print(f"    LINK SCRIPT: ({py_script_name}) -> PythonScript ({target_script_name})")
                else:
                    external_module_name = (
                        resolved_target_id  # This is a name string
                    )
                    # Basic filter for common non-module imports or too generic names
                    if (
                        external_module_name
                        and not external_module_name.startswith(".")
                        and len(external_module_name) > 1
                    ):
                        insert_external_module(conn, external_module_name)
                        create_external_import_relationship(
                            conn, py_abs_path_str, external_module_name
                        )
                        # print(f"    LINK EXTERNAL: ({py_script_name}) -> ExternalModule ({external_module_name})")

        except SyntaxError as e:
            print(f"SyntaxError parsing {py_abs_path_str}: {e}")
        except Exception as e:
            tb_lineno = (
                e.__traceback__.tb_lineno
                if hasattr(e, "__traceback__") and e.__traceback__
                else "N/A"
            )
            print(
                f"Error processing Python file {py_abs_path_str}: {type(e).__name__} - {e} (Line: {tb_lineno})"
            )

    print("\nDependency graph construction complete.")
    print(f"Kuzu database saved at: {DB_PATH}")
    print("\nSample queries to try:")
    print(
        "  MATCH (j:Job)-[]->(ps:PythonScript) RETURN j.command, j.type, ps.name;"
    )
    print(
        "  MATCH (ps1:PythonScript)-[:IMPORTS_SCRIPT]->(ps2:PythonScript) RETURN ps1.name, ps2.name;"
    )
    print(
        "  MATCH (ps:PythonScript)-[:IMPORTS_EXTERNAL]->(em:ExternalModule) RETURN ps.name, em.name;"
    )
    print(
        "  MATCH p = (j:Job {type:'bash'})-[*1..3]->(n) RETURN p LIMIT 10;"
    )  # Bash job and its dependencies
    print(
        "  MATCH (ps:PythonScript {name:'utils.py'})<-[:IMPORTS_SCRIPT*]-(importer:PythonScript) RETURN DISTINCT importer.name;"
    )  # Who imports utils.py (directly or indirectly)


if __name__ == "__main__":
    main()
    main()

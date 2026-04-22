import difflib
import os
import re
import subprocess
import tempfile
from flask import Flask, render_template, request

app = Flask(__name__)


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith('.c')


def run_clang(input_c_path: str, work_dir: str) -> tuple[str, str]:
    """Generate LLVM IR at -O0 and -O2 and return both as strings."""
    o0_path = os.path.join(work_dir, "O0.ll")
    o2_path = os.path.join(work_dir, "O2.ll")

    commands = [
        ["clang", "-O0", "-S", "-emit-llvm", input_c_path, "-o", o0_path],
        ["clang", "-O2", "-S", "-emit-llvm", input_c_path, "-o", o2_path],
    ]

    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "Clang failed. Ensure clang is installed and the input C file is valid.\n"
                f"Command: {' '.join(cmd)}\n"
                f"stderr: {result.stderr.strip()}"
            )

    with open(o0_path, "r", encoding="utf-8") as f:
        o0_ir = f.read()

    with open(o2_path, "r", encoding="utf-8") as f:
        o2_ir = f.read()

    return o0_ir, o2_ir


def generate_diff(o0_ir: str, o2_ir: str) -> str:
    """Return a unified text diff between O0 and O2 IR."""
    diff_lines = difflib.unified_diff(
        o0_ir.splitlines(),
        o2_ir.splitlines(),
        fromfile="O0.ll",
        tofile="O2.ll",
        lineterm="",
    )
    return "\n".join(diff_lines)


def explain_ir_changes(o0_ir: str, o2_ir: str, diff_text: str) -> str:
    """Explain likely optimizations and where UB assumptions may matter."""
    optimization_notes: list[str] = []
    ub_notes: list[str] = []

    o0_lines = o0_ir.splitlines()
    o2_lines = o2_ir.splitlines()

    arith_ops = r"\\b(add|sub|mul|sdiv|udiv|srem|urem)\\b"
    o0_arith_count = sum(1 for line in o0_lines if re.search(arith_ops, line))
    o2_arith_count = sum(1 for line in o2_lines if re.search(arith_ops, line))

    if o2_arith_count < o0_arith_count and re.search(r"ret\\s+\\w+\\s+[-]?\\d+", o2_ir):
        optimization_notes.append(
            "Constant folding: arithmetic appears reduced in O2, and a direct constant return/value is present."
        )

    o0_load_store = sum(1 for line in o0_lines if re.search(r"\\b(load|store)\\b", line))
    o2_load_store = sum(1 for line in o2_lines if re.search(r"\\b(load|store)\\b", line))
    if o2_load_store < o0_load_store:
        optimization_notes.append(
            "Memory-to-register promotion / simplification: fewer load/store operations appear in O2."
        )

    removed_non_metadata = [
        line[1:] for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    if len(removed_non_metadata) > 8:
        optimization_notes.append(
            "Dead code elimination and instruction cleanup: many instructions present in O0 are removed in O2."
        )

    if "nsw" in o2_ir or "nuw" in o2_ir:
        ub_notes.append(
            "Integer no-wrap flags (nsw/nuw) are present. LLVM may assume signed/unsigned overflow does not occur, "
            "which enables more aggressive optimizations."
        )

    if "add nsw" in o2_ir or "sub nsw" in o2_ir or "mul nsw" in o2_ir:
        ub_notes.append(
            "Signed overflow UB assumption: operations marked with nsw allow the compiler to optimize under the rule "
            "that signed overflow is undefined behavior in C."
        )

    if "undef" in o2_ir or "poison" in o2_ir:
        ub_notes.append(
            "`undef`/`poison` values appear in optimized IR. These often indicate places where the optimizer relies on "
            "undefined or impossible program states."
        )

    if not optimization_notes:
        optimization_notes.append(
            "General optimization happened between O0 and O2, but no specific heuristic strongly matched."
        )

    if not ub_notes:
        ub_notes.append(
            "No strong UB-specific pattern was detected by heuristics. This does not prove UB is absent."
        )

    explanation_lines = [
        "Optimization Detected:",
        *[f"- {note}" for note in optimization_notes],
        "",
        "Possible Undefined Behavior Impact:",
        *[f"- {note}" for note in ub_notes],
        "",
        "LLVM Assumption Reminder:",
        "- LLVM optimizations generally assume undefined behavior does not occur in valid C programs.",
    ]

    return "\n".join(explanation_lines)


def format_diff_html(diff_text: str) -> str:
    """Apply simple HTML formatting classes to diff lines."""
    escaped = []
    for line in diff_text.splitlines():
        safe = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            escaped.append(f'<span class="diff-meta">{safe}</span>')
        elif line.startswith("+"):
            escaped.append(f'<span class="diff-add">{safe}</span>')
        elif line.startswith("-"):
            escaped.append(f'<span class="diff-del">{safe}</span>')
        else:
            escaped.append(safe)
    return "\n".join(escaped)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    uploaded = request.files.get("cfile")
    if uploaded is None or uploaded.filename == "":
        return render_template("index.html", error="Please choose a C source file (.c).")

    if not allowed_file(uploaded.filename):
        return render_template("index.html", error="Only .c files are supported.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = os.path.join(tmp_dir, "input.c")
        uploaded.save(input_path)

        try:
            o0_ir, o2_ir = run_clang(input_path, tmp_dir)
            diff_text = generate_diff(o0_ir, o2_ir)
            explanation = explain_ir_changes(o0_ir, o2_ir, diff_text)
            diff_html = format_diff_html(diff_text)
        except RuntimeError as exc:
            return render_template("index.html", error=str(exc))

    return render_template(
        "result.html",
        o0_ir=o0_ir,
        o2_ir=o2_ir,
        diff_text=diff_text,
        diff_html=diff_html,
        explanation=explanation,
    )


if __name__ == "__main__":
    app.run(debug=True)

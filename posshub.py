#!/usr/bin/env python3
"""
PossHub — An opossum-themed git repository manager.
No Microsoft nastiness. Just possums.

Usage:
    python3 posshub.py [--port PORT]

Then open http://localhost:3000 in your browser.
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from html import escape
from pathlib import Path

BASE_DIR = Path(__file__).parent
REPOS_DIR = BASE_DIR / "repos"
STATIC_DIR = BASE_DIR / "static"
HOST = "127.0.0.1"
PORT = 3000

REPOS_DIR.mkdir(exist_ok=True)

OPOSSUM_FACTS = [
    "Opossums are immune to most snake venom.",
    "Opossums eat up to 5,000 ticks per season.",
    "Baby opossums are called joeys.",
    "Opossums have been around for 70 million years.",
    "Opossums have opposable thumbs on their hind feet.",
    "Playing dead is an involuntary response, not a choice.",
    "Opossums are North America's only marsupial.",
    "Opossums are naturally resistant to rabies.",
    "An opossum's tail is prehensile and can grip branches.",
    "Opossums have 50 teeth — more than any other North American land mammal.",
]

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_cmd(repo_path, *args):
    try:
        r = subprocess.run(
            ["git", "--git-dir", str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1


def list_repos():
    repos = []
    for item in sorted(REPOS_DIR.iterdir()):
        if not item.is_dir() or not (item / "HEAD").exists():
            continue
        name = item.name[:-4] if item.name.endswith(".git") else item.name
        desc_file = item / "description"
        desc = ""
        if desc_file.exists():
            desc = desc_file.read_text().strip()
            if desc.startswith("Unnamed repository"):
                desc = ""
        stdout, _, rc = git_cmd(item, "log", "-1", "--format=%H|%s|%an|%ar")
        last = None
        if rc == 0 and stdout.strip():
            p = stdout.strip().split("|", 3)
            if len(p) == 4:
                last = {"sha": p[0], "message": p[1], "author": p[2], "date": p[3]}
        repos.append(dict(name=name, dir_name=item.name, description=desc, last_commit=last))
    return repos


def default_branch(repo):
    out, _, rc = git_cmd(repo, "symbolic-ref", "--short", "HEAD")
    if rc == 0 and out.strip():
        return out.strip()
    out, _, rc = git_cmd(repo, "branch", "--format=%(refname:short)")
    for line in (out or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return "main"


def list_tree(repo, ref, path=""):
    target = f"{ref}:{path}" if path else ref
    out, _, rc = git_cmd(repo, "ls-tree", target)
    if rc != 0:
        return []
    items = []
    for line in out.strip().splitlines():
        if not line:
            continue
        meta, name = line.split("\t", 1)
        parts = meta.split()
        items.append(dict(mode=parts[0], type=parts[1], hash=parts[2], name=name))
    items.sort(key=lambda x: (0 if x["type"] == "tree" else 1, x["name"].lower()))
    return items


def read_blob(repo, ref, path):
    out, _, rc = git_cmd(repo, "show", f"{ref}:{path}")
    return out if rc == 0 else None


def commit_log(repo, ref="HEAD", count=50):
    out, _, rc = git_cmd(repo, "log", ref, f"-{count}",
                         "--format=%H|%s|%an|%ae|%ar")
    if rc != 0:
        return []
    commits = []
    for line in out.strip().splitlines():
        p = line.split("|", 4)
        if len(p) >= 5:
            commits.append(dict(sha=p[0], message=p[1], author=p[2],
                                email=p[3], date=p[4]))
    return commits


def show_commit(repo, sha):
    out, _, rc = git_cmd(repo, "log", "-1",
                         "--format=%H|%s|%an|%ae|%ar|%B", sha)
    if rc != 0:
        return None
    first_line = out.split("\n", 1)[0]
    p = first_line.split("|", 5)
    if len(p) < 6:
        return None
    stat_out, _, _ = git_cmd(repo, "diff-tree", "--stat", "--no-commit-id", sha)
    diff_out, _, _ = git_cmd(repo, "diff-tree", "-p", "--no-commit-id", sha)
    return dict(sha=p[0], message=p[1], author=p[2], email=p[3],
                date=p[4], body=p[5], stats=stat_out.strip(),
                diff=diff_out.strip())


def branch_list(repo):
    out, _, rc = git_cmd(repo, "branch", "--format=%(refname:short)")
    if rc != 0:
        return []
    return [b.strip() for b in out.strip().splitlines() if b.strip()]


def commit_count(repo, ref="HEAD"):
    out, _, rc = git_cmd(repo, "rev-list", "--count", ref)
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else 0


def find_readme(repo, ref):
    for item in list_tree(repo, ref):
        if item["type"] == "blob" and item["name"].lower().startswith("readme"):
            return item["name"], read_blob(repo, ref, item["name"])
    return None, None


def create_repo(name, description=""):
    dir_name = name if name.endswith(".git") else name + ".git"
    repo_path = REPOS_DIR / dir_name
    if repo_path.exists():
        return False, "A den with that name already exists!"
    subprocess.run(["git", "init", "--bare", str(repo_path)],
                   capture_output=True, check=True)
    if description:
        (repo_path / "description").write_text(description)
    return True, str(repo_path)


def delete_repo(dir_name):
    repo_path = REPOS_DIR / dir_name
    if not repo_path.exists() or not (repo_path / "HEAD").exists():
        return False
    shutil.rmtree(repo_path)
    return True


def repo_path_for(name):
    """Resolve a repo name to its directory, trying .git suffix."""
    for suffix in [".git", ""]:
        p = REPOS_DIR / (name + suffix)
        if p.is_dir() and (p / "HEAD").exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Minimal Markdown → HTML  (good enough for READMEs)
# ---------------------------------------------------------------------------

def md_to_html(text):
    if text is None:
        return ""
    lines = text.split("\n")
    html_parts = []
    in_code = False
    in_list = False
    list_type = None  # 'ul' or 'ol'

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("</code></pre>")
                in_code = False
            else:
                if in_list:
                    html_parts.append(f"</{list_type}>")
                    in_list = False
                html_parts.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            html_parts.append(escape(line))
            html_parts.append("\n")
            continue

        stripped = line.strip()

        # Close list if line is not a list item
        if in_list and not re.match(r"^(\d+\.\s|[-*+]\s)", stripped) and stripped:
            html_parts.append(f"</{list_type}>")
            in_list = False

        # Empty line
        if not stripped:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            continue

        # Headers
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            html_parts.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            continue

        # Unordered list
        m = re.match(r"^[-*+]\s+(.*)", stripped)
        if m:
            if not in_list or list_type != "ul":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append("<ul>")
                in_list = True
                list_type = "ul"
            html_parts.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # Ordered list
        m = re.match(r"^\d+\.\s+(.*)", stripped)
        if m:
            if not in_list or list_type != "ol":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append("<ol>")
                in_list = True
                list_type = "ol"
            html_parts.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", stripped):
            html_parts.append("<hr>")
            continue

        # Paragraph
        html_parts.append(f"<p>{_inline(stripped)}</p>")

    if in_code:
        html_parts.append("</code></pre>")
    if in_list:
        html_parts.append(f"</{list_type}>")
    return "\n".join(html_parts)


def _inline(text):
    """Handle inline markdown: bold, italic, code, links."""
    t = escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"__(.+?)__", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"_(.+?)_", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


# ---------------------------------------------------------------------------
# HTML layout & pages
# ---------------------------------------------------------------------------

import hashlib, time
_boot = int(time.time())


def _fact():
    idx = _boot % len(OPOSSUM_FACTS)
    return OPOSSUM_FACTS[idx]


def _layout(title, body, active=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)} - PossHub</title>
<link rel="icon" href="/static/logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav class="topnav">
  <a class="topnav-logo" href="/">
    <img src="/static/logo.svg" alt="PossHub">
    PossHub
  </a>
  <div class="topnav-links">
    <a href="/"{"class='active'" if active == "dens" else ""}>Dens</a>
    <a href="/new">Dig New Den</a>
    <a href="https://www.paypal.com/donate/?hosted_button_id=POSSHUB" class="btn-donate" target="_blank">Donate</a>
  </div>
</nav>
<div class="container">
{body}
</div>
<footer class="footer">
  <p>PossHub &mdash; Where code plays dead until it&rsquo;s ready.</p>
  <p style="margin-top:4px">Did you know? {escape(_fact())}</p>
  <p style="margin-top:8px">No Microsoft nastiness. 100% opossum powered.</p>
</footer>
</body>
</html>"""


def page_home(alert=""):
    repos = list_repos()
    alert_html = ""
    if alert:
        cls, msg = alert.split("|", 1) if "|" in alert else ("success", alert)
        alert_html = f'<div class="alert alert-{cls}">{escape(msg)}</div>'

    if not repos:
        body = f"""{alert_html}
<div class="hero">
  <img class="hero-logo" src="/static/logo.svg" alt="PossHub">
  <h1>Welcome to PossHub</h1>
  <p>"Where code plays dead until it&rsquo;s ready"</p>
  <div class="hero-donate">
    <a class="btn-donate-lg" href="https://www.paypal.com/donate/?hosted_button_id=POSSHUB" target="_blank">Donate via PayPal</a>
    <p>Help keep the possums fed and the servers running.</p>
  </div>
</div>
<div class="empty-state">
  <h2>Your den is empty!</h2>
  <p>No repositories yet. Time to forage for some code.</p>
  <a class="btn btn-primary" href="/new">Dig a New Den</a>
</div>"""
        return _layout("Home", body, "dens")

    cards = ""
    for r in repos:
        last = ""
        if r["last_commit"]:
            lc = r["last_commit"]
            last = f'<span>{escape(lc["author"])}</span><span>&middot;</span><span>{escape(lc["date"])}</span>'
        desc = f'<div class="repo-desc">{escape(r["description"])}</div>' if r["description"] else ""
        cards += f"""<div class="repo-card">
  <div>
    <a class="repo-name" href="/{escape(r['name'])}">{escape(r['name'])}</a>
    {desc}
    <div class="repo-meta">{last}</div>
  </div>
</div>\n"""

    body = f"""{alert_html}
<div class="section-header">
  <h2>Your Dens</h2>
  <a class="btn btn-primary" href="/new">Dig New Den</a>
</div>
<div class="repo-list">
{cards}
</div>
<div style="text-align:center;margin-top:32px;">
  <div class="hero-donate">
    <a class="btn-donate-lg" href="https://www.paypal.com/donate/?hosted_button_id=POSSHUB" target="_blank">Donate via PayPal</a>
    <p>Help keep the possums fed and the servers running.</p>
  </div>
</div>"""
    return _layout("Home", body, "dens")


def page_new(error=""):
    err = f'<div class="alert alert-error">{escape(error)}</div>' if error else ""
    body = f"""{err}
<div class="section-header"><h2>Dig a New Den</h2></div>
<div class="form-card">
<form method="POST" action="/new">
  <div class="form-group">
    <label>Den Name</label>
    <input type="text" name="name" placeholder="my-awesome-project" required
           pattern="[a-zA-Z0-9._-]+" title="Letters, numbers, dots, dashes, and underscores only">
    <small>Letters, numbers, dots, dashes, and underscores only.</small>
  </div>
  <div class="form-group">
    <label>Description <span style="color:var(--text-muted)">(optional)</span></label>
    <input type="text" name="description" placeholder="A cozy place for code to hibernate">
  </div>
  <button class="btn btn-primary" type="submit">Dig It!</button>
</form>
</div>
<div style="margin-top:24px;color:var(--text-muted);font-size:13px;">
  <p><strong>After creating your den, add it as a remote:</strong></p>
  <pre style="background:var(--bg-secondary);padding:12px 16px;border-radius:8px;margin-top:8px;font-size:13px;border:1px solid var(--border);color:var(--text-secondary);">git remote add posshub {escape(str(REPOS_DIR))}/&lt;name&gt;.git
git push posshub main</pre>
</div>"""
    return _layout("Dig New Den", body)


def page_repo(name, repo, ref, alert=""):
    branch = ref
    branches = branch_list(repo)
    items = list_tree(repo, ref)
    n_commits = commit_count(repo, ref)
    readme_name, readme_content = find_readme(repo, ref)

    alert_html = ""
    if alert:
        cls, msg = alert.split("|", 1) if "|" in alert else ("success", alert)
        alert_html = f'<div class="alert alert-{cls}">{escape(msg)}</div>'

    # Breadcrumb
    bc = f'<div class="breadcrumb"><a href="/{escape(name)}">{escape(name)}</a></div>'

    # Tabs
    tabs = f"""<div class="repo-tabs">
  <a class="active" href="/{escape(name)}">Code</a>
  <a href="/{escape(name)}/commits/{escape(branch)}">Commit Log ({n_commits})</a>
</div>"""

    # Stats
    stats = f"""<div class="stats-row">
  <span class="stat"><strong>{n_commits}</strong> commits</span>
  <span class="stat"><strong>{len(branches)}</strong> {"branch" if len(branches) == 1 else "branches"}</span>
  <span class="branch-badge">{escape(branch)}</span>
</div>"""

    # Clone box
    clone_path = str(repo)
    clone_box = f"""<div class="clone-box">
  <span class="label">Clone</span>
  <code>git clone {escape(clone_path)}</code>
</div>"""

    if not items and not readme_content:
        empty = f"""<div class="empty-state">
  <span class="possum-emoji">&#x1F9A8;</span>
  <h2>This den is empty</h2>
  <p>Push some code to get started!</p>
  <pre style="background:var(--bg-secondary);padding:16px;border-radius:8px;text-align:left;display:inline-block;font-size:13px;border:1px solid var(--border);color:var(--text-secondary);">git remote add posshub {escape(clone_path)}
git push posshub {escape(branch)}</pre>
</div>"""
        body = f"{alert_html}{bc}{tabs}{stats}{clone_box}{empty}"
        return _layout(name, body)

    # File tree
    rows = ""
    for item in items:
        icon_cls = "dir" if item["type"] == "tree" else "file"
        icon = "&#x1F4C1;" if item["type"] == "tree" else "&#x1F4C4;"
        if item["type"] == "tree":
            href = f"/{escape(name)}/tree/{escape(branch)}/{escape(item['name'])}"
        else:
            href = f"/{escape(name)}/blob/{escape(branch)}/{escape(item['name'])}"
        rows += f"""<div class="file-row">
  <span class="file-icon {icon_cls}">{icon}</span>
  <a href="{href}">{escape(item['name'])}</a>
</div>\n"""
    tree = f'<div class="file-tree">{rows}</div>'

    # README
    readme_html = ""
    if readme_name and readme_content is not None:
        rendered = md_to_html(readme_content) if readme_name.lower().endswith(".md") else f"<pre>{escape(readme_content)}</pre>"
        readme_html = f"""<div class="readme-box">
  <div class="readme-header">{escape(readme_name)}</div>
  <div class="readme-body">{rendered}</div>
</div>"""

    # Delete button
    delete_btn = f"""<div style="margin-top:40px;padding-top:20px;border-top:1px solid var(--border);">
  <details>
    <summary style="color:var(--text-muted);cursor:pointer;font-size:13px;">Danger Zone</summary>
    <div style="margin-top:12px;">
      <form method="POST" action="/{escape(name)}/delete"
            onsubmit="return confirm('Are you sure you want to abandon this den? This cannot be undone!');">
        <button class="btn btn-danger btn-sm" type="submit">Abandon Den</button>
      </form>
    </div>
  </details>
</div>"""

    body = f"{alert_html}{bc}{tabs}{stats}{clone_box}{tree}{readme_html}{delete_btn}"
    return _layout(name, body)


def page_tree(name, repo, ref, path):
    items = list_tree(repo, ref, path)
    # Breadcrumb
    parts = path.strip("/").split("/")
    bc_links = [f'<a href="/{escape(name)}">{escape(name)}</a>', '<span class="sep">/</span>']
    accumulated = ""
    for i, part in enumerate(parts):
        accumulated = f"{accumulated}/{part}" if accumulated else part
        if i < len(parts) - 1:
            bc_links.append(f'<a href="/{escape(name)}/tree/{escape(ref)}/{escape(accumulated)}">{escape(part)}</a>')
            bc_links.append('<span class="sep">/</span>')
        else:
            bc_links.append(f'<span class="current">{escape(part)}</span>')
    bc = f'<div class="breadcrumb">{"".join(bc_links)}</div>'

    tabs = f"""<div class="repo-tabs">
  <a class="active" href="/{escape(name)}">Code</a>
  <a href="/{escape(name)}/commits/{escape(ref)}">Commit Log</a>
</div>"""

    rows = ""
    # Add parent link
    parent = "/".join(parts[:-1])
    if parent:
        parent_href = f"/{escape(name)}/tree/{escape(ref)}/{escape(parent)}"
    else:
        parent_href = f"/{escape(name)}"
    rows += f"""<div class="file-row">
  <span class="file-icon dir">&#x1F519;</span>
  <a href="{parent_href}">..</a>
</div>\n"""

    for item in items:
        icon_cls = "dir" if item["type"] == "tree" else "file"
        icon = "&#x1F4C1;" if item["type"] == "tree" else "&#x1F4C4;"
        full_path = f"{path}/{item['name']}" if path else item["name"]
        if item["type"] == "tree":
            href = f"/{escape(name)}/tree/{escape(ref)}/{escape(full_path)}"
        else:
            href = f"/{escape(name)}/blob/{escape(ref)}/{escape(full_path)}"
        rows += f"""<div class="file-row">
  <span class="file-icon {icon_cls}">{icon}</span>
  <a href="{href}">{escape(item['name'])}</a>
</div>\n"""

    tree = f'<div class="file-tree">{rows}</div>' if rows else '<div class="empty-state"><p>Empty directory</p></div>'
    body = f"{bc}{tabs}{tree}"
    return _layout(f"{name}/{path}", body)


def page_blob(name, repo, ref, path):
    content = read_blob(repo, ref, path)
    if content is None:
        return page_404()

    filename = path.split("/")[-1]
    parts = path.strip("/").split("/")
    bc_links = [f'<a href="/{escape(name)}">{escape(name)}</a>', '<span class="sep">/</span>']
    accumulated = ""
    for i, part in enumerate(parts):
        accumulated = f"{accumulated}/{part}" if accumulated else part
        if i < len(parts) - 1:
            bc_links.append(f'<a href="/{escape(name)}/tree/{escape(ref)}/{escape(accumulated)}">{escape(part)}</a>')
            bc_links.append('<span class="sep">/</span>')
        else:
            bc_links.append(f'<span class="current">{escape(part)}</span>')
    bc = f'<div class="breadcrumb">{"".join(bc_links)}</div>'

    tabs = f"""<div class="repo-tabs">
  <a class="active" href="/{escape(name)}">Code</a>
  <a href="/{escape(name)}/commits/{escape(ref)}">Commit Log</a>
</div>"""

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    size = len(content.encode("utf-8", errors="replace"))
    size_str = f"{size} bytes" if size < 1024 else f"{size/1024:.1f} KB"

    # Render with line numbers
    lines_html = ""
    for i, line in enumerate(content.split("\n"), 1):
        if i > line_count and not line:
            break
        lines_html += f'<div class="line"><span class="ln">{i}</span><span class="lc">{escape(line)}</span></div>\n'

    viewer = f"""<div class="file-viewer">
  <div class="file-viewer-header">
    <span>{escape(filename)} &middot; {line_count} lines &middot; {size_str}</span>
    <span class="branch-badge">{escape(ref)}</span>
  </div>
  <div class="file-content">
    <div class="line-numbers">
{lines_html}
    </div>
  </div>
</div>"""

    body = f"{bc}{tabs}{viewer}"
    return _layout(f"{name}/{filename}", body)


def page_commits(name, repo, ref):
    commits = commit_log(repo, ref)
    bc = f'<div class="breadcrumb"><a href="/{escape(name)}">{escape(name)}</a> <span class="sep">/</span> <span class="current">commits</span></div>'

    tabs = f"""<div class="repo-tabs">
  <a href="/{escape(name)}">Code</a>
  <a class="active" href="/{escape(name)}/commits/{escape(ref)}">Commit Log ({len(commits)})</a>
</div>"""

    if not commits:
        body = f"""{bc}{tabs}<div class="empty-state">
  <h2>No commits yet</h2>
  <p>This den is waiting for its first contribution.</p>
</div>"""
        return _layout(f"{name} - Commits", body)

    rows = ""
    for c in commits:
        rows += f"""<div class="commit-row">
  <div class="commit-msg"><a href="/{escape(name)}/commit/{c['sha']}">{escape(c['message'])}</a></div>
  <span class="commit-author">{escape(c['author'])}</span>
  <a class="commit-sha" href="/{escape(name)}/commit/{c['sha']}">{c['sha'][:7]}</a>
  <span class="commit-date">{escape(c['date'])}</span>
</div>\n"""

    body = f"""{bc}{tabs}
<div class="commit-list">{rows}</div>"""
    return _layout(f"{name} - Commits", body)


def page_commit_detail(name, repo, sha):
    data = show_commit(repo, sha)
    if not data:
        return page_404()

    bc = f"""<div class="breadcrumb">
  <a href="/{escape(name)}">{escape(name)}</a>
  <span class="sep">/</span>
  <a href="/{escape(name)}/commits/{escape(default_branch(repo))}">commits</a>
  <span class="sep">/</span>
  <span class="current">{sha[:7]}</span>
</div>"""

    detail = f"""<div class="commit-detail">
  <h2>{escape(data['message'])}</h2>
  <div class="commit-detail-meta">
    <span>{escape(data['author'])} &lt;{escape(data['email'])}&gt;</span>
    <span>{escape(data['date'])}</span>
    <span class="commit-sha">{sha[:10]}</span>
  </div>
</div>"""

    # Parse and render diff
    diff_html = _render_diff(data.get("diff", ""))

    body = f"{bc}{detail}{diff_html}"
    return _layout(f"{name} - {sha[:7]}", body)


def _render_diff(diff_text):
    if not diff_text:
        return '<div class="empty-state"><p>No file changes in this commit.</p></div>'

    html_parts = []
    current_file = None
    current_lines = []

    def flush():
        nonlocal current_file, current_lines
        if current_file:
            lines_html = "\n".join(current_lines)
            html_parts.append(f"""<div class="diff-file">
  <div class="diff-file-header">{escape(current_file)}</div>
  <div class="diff-content"><pre>{lines_html}</pre></div>
</div>""")
        current_file = None
        current_lines = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            flush()
            # Extract filename
            m = re.search(r"b/(.+)$", line)
            current_file = m.group(1) if m else line
            continue
        if line.startswith("index ") or line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            current_lines.append(f'<div class="diff-line hunk">{escape(line)}</div>')
        elif line.startswith("+"):
            current_lines.append(f'<div class="diff-line add">{escape(line)}</div>')
        elif line.startswith("-"):
            current_lines.append(f'<div class="diff-line del">{escape(line)}</div>')
        else:
            current_lines.append(f'<div class="diff-line ctx">{escape(line)}</div>')

    flush()
    return "\n".join(html_parts)


def page_404():
    body = """<div class="dead-possum">
  <h1>404</h1>
  <h2>Playing Dead</h2>
  <p>This page is playing possum. It might not exist, or it might just be pretending.</p>
  <a class="btn" href="/">Scurry Home</a>
</div>"""
    return _layout("404 - Playing Dead", body)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class PossHandler(http.server.BaseHTTPRequestHandler):
    server_version = "PossHub/1.0"

    def log_message(self, fmt, *args):
        # Cute logging
        print(f"  \033[90m{self.address_string()}\033[0m {fmt % args}")

    def _send(self, html_content, status=200, content_type="text/html"):
        data = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _read_post(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        return dict(urllib.parse.parse_qsl(body))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = dict(urllib.parse.parse_qsl(parsed.query))

        # Static files
        if path.startswith("/static/"):
            self._serve_static(path[8:])
            return

        # Home
        if path == "/":
            self._send(page_home(alert=query.get("alert", "")))
            return

        # New repo form
        if path == "/new":
            self._send(page_new())
            return

        # Route: /<repo>/tree/<ref>/<path...>
        m = re.match(r"^/([^/]+)/tree/([^/]+)(?:/(.+))?$", path)
        if m:
            name, ref, subpath = m.group(1), m.group(2), m.group(3) or ""
            repo = repo_path_for(name)
            if repo:
                self._send(page_tree(name, repo, ref, subpath))
                return

        # Route: /<repo>/blob/<ref>/<path...>
        m = re.match(r"^/([^/]+)/blob/([^/]+)/(.+)$", path)
        if m:
            name, ref, subpath = m.group(1), m.group(2), m.group(3)
            repo = repo_path_for(name)
            if repo:
                self._send(page_blob(name, repo, ref, subpath))
                return

        # Route: /<repo>/commits/<ref>
        m = re.match(r"^/([^/]+)/commits/([^/]+)$", path)
        if m:
            name, ref = m.group(1), m.group(2)
            repo = repo_path_for(name)
            if repo:
                self._send(page_commits(name, repo, ref))
                return

        # Route: /<repo>/commit/<sha>
        m = re.match(r"^/([^/]+)/commit/([a-f0-9]+)$", path)
        if m:
            name, sha = m.group(1), m.group(2)
            repo = repo_path_for(name)
            if repo:
                self._send(page_commit_detail(name, repo, sha))
                return

        # Route: /<repo>  (repo overview)
        m = re.match(r"^/([^/]+)$", path)
        if m:
            name = m.group(1)
            repo = repo_path_for(name)
            if repo:
                ref = default_branch(repo)
                self._send(page_repo(name, repo, ref, alert=query.get("alert", "")))
                return

        self._send(page_404(), 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Create repo
        if path == "/new":
            data = self._read_post()
            name = data.get("name", "").strip()
            desc = data.get("description", "").strip()
            if not name or not re.match(r"^[a-zA-Z0-9._-]+$", name):
                self._send(page_new(error="Invalid den name. Letters, numbers, dots, dashes, underscores only."))
                return
            ok, msg = create_repo(name, desc)
            if ok:
                self._redirect(f"/{name}?alert=success|Den+dug+successfully!+Welcome+to+your+new+den.")
            else:
                self._send(page_new(error=msg))
            return

        # Delete repo
        m = re.match(r"^/([^/]+)/delete$", path)
        if m:
            name = m.group(1)
            repo = repo_path_for(name)
            if repo:
                delete_repo(repo.name)
                self._redirect("/?alert=success|Den+abandoned.+The+opossum+has+moved+on.")
            else:
                self._send(page_404(), 404)
            return

        self._send(page_404(), 404)

    def _serve_static(self, filename):
        # Prevent path traversal
        safe = Path(filename).name
        filepath = STATIC_DIR / safe
        if not filepath.exists() or not filepath.is_file():
            self._send(page_404(), 404)
            return

        ext = filepath.suffix.lower()
        content_types = {
            ".css": "text/css",
            ".js": "application/javascript",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        ct = content_types.get(ext, "application/octet-stream")

        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import sys
    port = PORT
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--port" and i + 1 < len(sys.argv) - 1:
            port = int(sys.argv[i + 2])

    server = http.server.HTTPServer((HOST, port), PossHandler)
    print(f"""
\033[1m
    ____                 __  __      __
   / __ \\____  _________/ / / /_  __/ /_
  / /_/ / __ \\/ ___/ ___/ /_/ / / / / __ \\
 / ____/ /_/ (__  |__  ) __  / /_/ / /_/ /
/_/    \\____/____/____/_/ /_/\\__,_/_.___/
\033[0m
\033[32m  Where code plays dead until it's ready.\033[0m
\033[90m  No Microsoft nastiness. 100% opossum powered.\033[0m

  Serving on \033[1mhttp://{HOST}:{port}\033[0m
  Dens stored in \033[90m{REPOS_DIR}\033[0m

  Press Ctrl+C to scurry away.
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  The opossum has played dead. Goodbye!")
        server.server_close()


if __name__ == "__main__":
    main()

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
REPO_COUNT = int(os.environ.get("REPO_COUNT", "5"))
README_PATH = os.environ.get("README_PATH", "README.md")
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


class GitHubClient:
    """Minimal HTTP client for the GitHub API (headers, pagination, retry)."""

    def __init__(self, username):
        self.username = username
        self.token = os.environ.get("GH_TOKEN")

    def _headers(self):
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, url, params):
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT)
                if response.status_code == 403 and "rate limit" in response.text.lower():
                    raise RuntimeError(
                        "⛔ GitHub rate limit reached. Add/renew GH_TOKEN to raise the quota."
                    )
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as err:
                last_error = err
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise RuntimeError(f"Call to {url} failed after {MAX_RETRIES} attempts: {last_error}")

    def _fetch_paginated(self, url, params=None):
        """Fetch all pages from a GitHub API endpoint."""
        results = []
        params = dict(params or {})
        params["per_page"] = 100
        page = 1
        while True:
            params["page"] = page
            data = self._get(url, params)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results

    def get_orgs(self):
        """Fetch the authenticated user's orgs (requires GH_TOKEN)."""
        if not self.token:
            print("⚠️  No GH_TOKEN: organization repos will not be included.")
            return []
        try:
            return self._fetch_paginated("https://api.github.com/user/orgs")
        except RuntimeError as err:
            print(f"⚠️  Could not fetch orgs: {err}")
            return []

    def get_personal_repos(self):
        return self._fetch_paginated(
            f"https://api.github.com/users/{self.username}/repos",
            params={"sort": "pushed", "direction": "desc", "type": "owner"},
        )

    def get_org_repos(self, org_name):
        return self._fetch_paginated(f"https://api.github.com/orgs/{org_name}/repos")

    def get_all_repos(self):
        """Fetch personal repos plus repos from every accessible org."""
        repos = self.get_personal_repos()
        for org in self.get_orgs():
            repos.extend(self.get_org_repos(org["login"]))
        return repos


def select_recent_repos(repos, username, count):
    """Deduplicate, filter (forks/archived/profile) and keep the most recent."""
    seen = set()
    filtered = []
    for repo in repos:
        if repo["id"] in seen:
            continue
        seen.add(repo["id"])
        if repo.get("fork") or repo.get("archived"):
            continue
        if repo["name"].lower() == username.lower():
            continue
        filtered.append(repo)

    filtered.sort(key=lambda r: r["pushed_at"], reverse=True)
    return filtered[:count]


def build_table(repos, username):
    rows = [
        "| Project | Description | Language | ⭐ | Last Updated |",
        "|---------|--------------|----------|-----|--------------|",
    ]
    for repo in repos:
        name = repo["name"]
        url = repo["html_url"]
        desc = (repo["description"] or "—").replace("|", "\\|")
        lang = repo["language"] or "—"
        stars = repo["stargazers_count"]
        pushed = datetime.strptime(repo["pushed_at"], "%Y-%m-%dT%H:%M:%SZ").strftime("%d/%m/%Y")
        owner = repo["owner"]["login"]
        display_name = f"{owner}/{name}" if owner.lower() != username.lower() else name
        rows.append(f"| [{display_name}]({url}) | {desc} | `{lang}` | {stars} | {pushed} |")
    return "\n".join(rows)


def splice_section(content, start_marker, end_marker, new_body):
    """Replace the content between two markers (markers are kept)."""
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        missing = [m for m, i in [(start_marker, start_idx), (end_marker, end_idx)] if i == -1]
        raise RuntimeError(f"Missing marker(s): {missing}")

    start_idx += len(start_marker)
    if start_idx > end_idx:
        raise RuntimeError("Markers are in the wrong order.")

    return content[:start_idx] + new_body + content[end_idx:]


def update_readme(table):
    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    utc_plus_1 = timezone(timedelta(hours=1))
    now = datetime.now(timezone.utc).astimezone(utc_plus_1)
    now_str = now.strftime("%d/%m/%Y at %H:%M UTC+1")
    new_body = f"\n{table}\n\n> 🕐 Last updated: {now_str}\n"

    new_content = splice_section(content, "<!-- PROJECTS-START -->", "<!-- PROJECTS-END -->", new_body)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ README updated with {REPO_COUNT} recent projects (personal + orgs).")


def main():
    client = GitHubClient(GITHUB_USERNAME)
    repos = client.get_all_repos()
    recent_repos = select_recent_repos(repos, GITHUB_USERNAME, REPO_COUNT)
    table = build_table(recent_repos, GITHUB_USERNAME)
    update_readme(table)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Failed to update README: {e}")
        sys.exit(1)

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
    """Client HTTP minimal pour l'API GitHub (headers, pagination, retry)."""

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
                        "⛔ Rate limit GitHub atteinte. Ajoute/renouvelle GH_TOKEN pour augmenter le quota."
                    )
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as err:
                last_error = err
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise RuntimeError(f"Échec de l'appel à {url} après {MAX_RETRIES} tentatives : {last_error}")

    def _fetch_paginated(self, url, params=None):
        """Récupère toutes les pages d'un endpoint GitHub."""
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
        """Récupère les orgs de l'utilisateur authentifié (nécessite GH_TOKEN)."""
        if not self.token:
            print("⚠️  Pas de GH_TOKEN : les repos d'organisations ne seront pas inclus.")
            return []
        try:
            return self._fetch_paginated("https://api.github.com/user/orgs")
        except RuntimeError as err:
            print(f"⚠️  Impossible de récupérer les orgs : {err}")
            return []

    def get_personal_repos(self):
        return self._fetch_paginated(
            f"https://api.github.com/users/{self.username}/repos",
            params={"sort": "pushed", "direction": "desc", "type": "owner"},
        )

    def get_org_repos(self, org_name):
        return self._fetch_paginated(f"https://api.github.com/orgs/{org_name}/repos")

    def get_all_repos(self):
        """Récupère les repos perso + ceux de toutes les orgs accessibles."""
        repos = self.get_personal_repos()
        for org in self.get_orgs():
            repos.extend(self.get_org_repos(org["login"]))
        return repos


def select_recent_repos(repos, username, count):
    """Dédoublonne, filtre (forks/archivés/profil) et garde les plus récents."""
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
        "| Projet | Description | Langage | ⭐ | Dernière MàJ |",
        "|--------|-------------|---------|-----|--------------|",
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
    """Remplace le contenu entre deux marqueurs (marqueurs conservés)."""
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        missing = [m for m, i in [(start_marker, start_idx), (end_marker, end_idx)] if i == -1]
        raise RuntimeError(f"Marqueur(s) manquant(s) : {missing}")

    start_idx += len(start_marker)
    if start_idx > end_idx:
        raise RuntimeError("Les marqueurs sont dans le mauvais ordre.")

    return content[:start_idx] + new_body + content[end_idx:]


def update_readme(table):
    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    utc_plus_1 = timezone(timedelta(hours=1))
    now = datetime.now(timezone.utc).astimezone(utc_plus_1)
    now_str = now.strftime("%d/%m/%Y à %H:%M UTC+1")
    new_body = f"\n{table}\n\n> 🕐 Dernière mise à jour : {now_str}\n"

    new_content = splice_section(content, "<!-- PROJECTS-START -->", "<!-- PROJECTS-END -->", new_body)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ README mis à jour avec {REPO_COUNT} projets récents (perso + orgs).")


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
        print(f"❌ Échec de la mise à jour du README : {e}")
        sys.exit(1)

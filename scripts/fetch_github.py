"""Fetch contribution calendar and public repos, write data/github.json.

Run locally with `GH_TOKEN=$(gh auth token) python scripts/fetch_github.py`,
or let .github/workflows/refresh-data.yml run it daily.

The token matters: a personal token with `read:user` returns private
contributions in the calendar total, which is where most of the work lives.
The default Actions token only sees public activity.
"""

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

USER = os.environ.get("GH_USER", "MR-Axel")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "github.json"
# Public repos that live in an organization and still belong in the list, as "owner/name".
EXTRA = [x for x in os.environ.get("GH_EXTRA_REPOS", "SharpMD/sharpmd").split(",") if "/" in x]

REPO_FIELDS = """
        name description url stargazerCount pushedAt isArchived isPrivate
        primaryLanguage { name }
        repositoryTopics(first: 10) { nodes { topic { name } } }
"""

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, privacy: PUBLIC, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {""" + REPO_FIELDS + """      }
    }
  }
""" + "".join(
    '  extra%d: repository(owner: "%s", name: "%s") {%s  }\n' % ((i,) + tuple(x.strip().split("/", 1)) + (REPO_FIELDS,))
    for i, x in enumerate(EXTRA)
) + """}
"""


def graphql():
    if not TOKEN:
        sys.exit("no GH_TOKEN / GITHUB_TOKEN in the environment")
    body = json.dumps({"query": QUERY, "variables": {"login": USER}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": "bearer " + TOKEN,
            "Content-Type": "application/json",
            "User-Agent": USER + "-site",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        sys.exit("GitHub API error: " + json.dumps(payload["errors"]))
    user = payload["data"]["user"]
    # The repos of an organization go in with the rest, newest push first.
    extra = [payload["data"].get("extra%d" % i) for i in range(len(EXTRA))]
    nodes = user["repositories"]["nodes"] + [r for r in extra if r and not r["isPrivate"] and not r["isArchived"]]
    seen = set()
    user["repositories"]["nodes"] = [
        r for r in sorted(nodes, key=lambda r: r["pushedAt"] or "", reverse=True)
        if not (r["url"] in seen or seen.add(r["url"]))
    ]
    return user



def main():
    user = graphql()
    calendar = user["contributionsCollection"]["contributionCalendar"]
    days = [d for week in calendar["weeks"] for d in week["contributionDays"]]

    data = {
        "generated": days[-1]["date"] if days else None,
        "contributions": {
            "total": calendar["totalContributions"],
            "days": [{"d": d["date"], "c": d["contributionCount"]} for d in days],
        },
        "repos": [
            {
                "name": r["name"],
                "description": r["description"],
                "url": r["url"],
                "language": (r["primaryLanguage"] or {}).get("name"),
                "stars": r["stargazerCount"],
                # pushedAt, not updatedAt: editing a description should not
                # make a 2020 repo look like this week's work
                "updated": r["pushedAt"],
                "archived": r["isArchived"],
                "topics": [t["topic"]["name"] for t in r["repositoryTopics"]["nodes"]],
            }
            for r in user["repositories"]["nodes"]
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("wrote %s: %d days, %d contributions, %d repos"
          % (OUT.name, len(days), data["contributions"]["total"], len(data["repos"])))



if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        sys.exit("HTTP %s from the GitHub API" % exc.code)

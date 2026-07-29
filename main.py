#!/usr/bin/env python3
"""Generate a neofetch-style GitHub profile SVG with live stats.

Env vars (set by the GitHub Action):
  ACCESS_TOKEN  - a classic PAT with scopes: repo, read:user
  USER_NAME     - your GitHub login (github.repository_owner)
  UPTIME_SINCE  - optional YYYY-MM-DD; defaults to your account creation date
"""
import os, json, time, datetime
import requests
from dateutil.relativedelta import relativedelta

TOKEN = os.environ.get("ACCESS_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit(
        "ACCESS_TOKEN is empty. Add it under Settings > Secrets and variables > "
        "Actions > Secrets (not Variables), named exactly ACCESS_TOKEN."
    )

USERNAME     = os.environ["USER_NAME"]
UPTIME_SINCE = os.environ.get("UPTIME_SINCE") or None

API        = "https://api.github.com/graphql"
HEADERS    = {"Authorization": f"bearer {TOKEN}", "User-Agent": USERNAME}
CACHE_FILE = "cache/loc.json"
TEMPLATE   = "template.svg"
ASCII_ART  = "ascii_art.txt"
OUTPUT     = "profile.svg"


def gql(query, variables=None, retries=6):
    last = None
    for i in range(retries):
        r = requests.post(API, json={"query": query, "variables": variables or {}},
                          headers=HEADERS, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if "errors" in data:
                blob = json.dumps(data["errors"]).lower()
                if any(t in blob for t in ("rate_limited", "rate limit", "timeout", "502", "503")):
                    last = data["errors"]; time.sleep(2 ** i); continue   # transient, retry
                raise RuntimeError(f"GraphQL error: {data['errors']}")
            return data["data"]
        # Bad credentials / permissions -> no point retrying
        if r.status_code == 401 or (r.status_code == 403 and "rate limit" not in r.text.lower()):
            raise RuntimeError(
                f"Auth failed (HTTP {r.status_code}). The ACCESS_TOKEN GitHub received is "
                f"missing, expired, or lacks the 'repo' + 'read:user' scopes.\n{r.text[:200]}"
            )
        last = f"HTTP {r.status_code}: {r.text[:200]}"     # transient (5xx / secondary limit)
        time.sleep(2 ** i)
    raise RuntimeError(f"GraphQL failed after {retries} tries: {last}")


def user_overview():
    q = """
    query($login:String!){
      user(login:$login){
        id
        createdAt
        followers { totalCount }
        contrib: repositoriesContributedTo(
          includeUserRepositories:false,
          contributionTypes:[COMMIT, PULL_REQUEST, REPOSITORY]
        ){ totalCount }
      }
    }"""
    return gql(q, {"login": USERNAME})["user"]


def owned_repos():
    q = """
    query($login:String!, $cursor:String){
      user(login:$login){
        repositories(first:100, after:$cursor, ownerAffiliations:OWNER, isFork:false,
                     orderBy:{field:PUSHED_AT, direction:DESC}){
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            nameWithOwner
            stargazerCount
            defaultBranchRef { name target { oid } }
          }
        }
      }
    }"""
    repos, cursor, total = [], None, 0
    while True:
        page = gql(q, {"login": USERNAME, "cursor": cursor})["user"]["repositories"]
        total = page["totalCount"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return total, repos


def total_commits(created_at):
    q = """
    query($login:String!, $from:DateTime!, $to:DateTime!){
      user(login:$login){
        contributionsCollection(from:$from, to:$to){
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }"""
    start = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now   = datetime.datetime.now(datetime.timezone.utc)
    total, cur = 0, start
    while cur < now:
        nxt = min(cur + relativedelta(years=1), now)
        c = gql(q, {"login": USERNAME, "from": cur.isoformat(), "to": nxt.isoformat()})
        cc = c["user"]["contributionsCollection"]
        total += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
        cur = nxt
    return total


def repo_loc(owner, name, branch, author_id):
    q = """
    query($owner:String!, $name:String!, $branch:String!, $id:ID!, $cursor:String){
      repository(owner:$owner, name:$name){
        object(expression:$branch){
          ... on Commit {
            history(first:100, author:{id:$id}, after:$cursor){
              pageInfo { hasNextPage endCursor }
              nodes { additions deletions }
            }
          }
        }
      }
    }"""
    add = dele = 0
    cursor = None
    while True:
        obj = gql(q, {"owner": owner, "name": name, "branch": branch,
                      "id": author_id, "cursor": cursor})["repository"]["object"]
        if not obj:
            break
        hist = obj["history"]
        for n in hist["nodes"]:
            add  += n["additions"]
            dele += n["deletions"]
        if not hist["pageInfo"]["hasNextPage"]:
            break
        cursor = hist["pageInfo"]["endCursor"]
    return add, dele


def lines_of_code(repos, author_id):
    try:
        cache = json.load(open(CACHE_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    add = dele = 0
    for repo in repos:
        ref = repo.get("defaultBranchRef")
        if not ref or not ref.get("target"):      # empty repo
            continue
        key, head = repo["nameWithOwner"], ref["target"]["oid"]
        cached = cache.get(key)
        if cached and cached.get("oid") == head:   # unchanged -> reuse
            a, d = cached["add"], cached["del"]
        else:
            owner, name = key.split("/", 1)
            a, d = repo_loc(owner, name, ref["name"], author_id)
            cache[key] = {"oid": head, "add": a, "del": d}
        add += a; dele += d
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    json.dump(cache, open(CACHE_FILE, "w"), indent=1)
    return add, dele


def fmt_uptime(created_at):
    start = datetime.date.fromisoformat((UPTIME_SINCE or created_at[:10]))
    rd = relativedelta(datetime.date.today(), start)
    parts = []
    if rd.years:  parts.append(f"{rd.years} year"  + ("s" if rd.years  != 1 else ""))
    if rd.months: parts.append(f"{rd.months} month"+ ("s" if rd.months != 1 else ""))
    parts.append(f"{rd.days} day" + ("s" if rd.days != 1 else ""))
    return ", ".join(parts)


def ascii_tspans():
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = open(ASCII_ART, encoding="utf-8").read().split("\n")
    return "".join(
        f'<tspan x="22" dy="{0 if i == 0 else 10}">{esc(ln) or " "}</tspan>'
        for i, ln in enumerate(lines)
    )


def main():
    u = user_overview()
    repo_count, repos = owned_repos()
    add, dele = lines_of_code(repos, u["id"])
    values = {
        "{{ASCII_TSPANS}}": ascii_tspans(),
        "{{UPTIME}}":       fmt_uptime(u["createdAt"]),
        "{{REPOS}}":        f'{repo_count:,}',
        "{{CONTRIB}}":      f'{u["contrib"]["totalCount"]:,}',
        "{{COMMITS}}":      f'{total_commits(u["createdAt"]):,}',
        "{{STARS}}":        f'{sum(r["stargazerCount"] for r in repos):,}',
        "{{FOLLOWERS}}":    f'{u["followers"]["totalCount"]:,}',
        "{{LOC}}":          f'{add - dele:,}',
        "{{ADD}}":          f'{add:,}',
        "{{DEL}}":          f'{dele:,}',
    }
    svg = open(TEMPLATE, encoding="utf-8").read()
    for k, v in values.items():
        svg = svg.replace(k, v)
    open(OUTPUT, "w", encoding="utf-8").write(svg)
    print("Wrote", OUTPUT, "|", {k: v for k, v in values.items() if k != "{{ASCII_TSPANS}}"})


if __name__ == "__main__":
    main()

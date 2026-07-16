"""Sync a pushed branch with its Linear issue: add a comment, and move the
issue to an "In Review" state the first time a PR is opened for it.

Invoked by .github/workflows/pr-linear-sync.yml. Relies on Linear's API
accepting an issue's human-readable identifier (e.g. "JKR-101") anywhere a
UUID is otherwise expected.
"""

import json
import os
import sys
import urllib.error
import urllib.request

LINEAR_API_URL = "https://api.linear.app/graphql"
REVIEW_STATE_NAMES = ("in review", "review")


def gql(api_key: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        LINEAR_API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"Linear API request failed: {exc.code} {exc.read().decode()}")
        sys.exit(1)

    if payload.get("errors"):
        return payload
    return payload["data"]


def main() -> None:
    api_key = os.environ["LINEAR_API_KEY"]
    issue_key = os.environ["ISSUE_KEY"]
    pr_url = os.environ.get("PR_URL", "")
    pr_created = os.environ.get("PR_CREATED") == "true"
    commit_sha = os.environ["COMMIT_SHA"][:7]
    commit_url = os.environ.get("COMMIT_URL", "")
    actor = os.environ["ACTOR"]

    issue_data = gql(
        api_key,
        "query($id: String!) { issue(id: $id) { id identifier team { id } } }",
        {"id": issue_key},
    )
    if issue_data.get("errors") or not issue_data.get("issue"):
        print(f"No Linear issue found for '{issue_key}', skipping sync.")
        return
    issue = issue_data["issue"]

    comment_lines = [f"Pushed by {actor}: [`{commit_sha}`]({commit_url})"]
    if pr_url:
        comment_lines.append(
            f"Pull request {'opened' if pr_created else 'updated'}: {pr_url}"
        )
    gql(
        api_key,
        "mutation($issueId: String!, $body: String!) { "
        "commentCreate(input: { issueId: $issueId, body: $body }) { success } }",
        {"issueId": issue["id"], "body": "\n".join(comment_lines)},
    )

    if not pr_created:
        return

    states_data = gql(
        api_key,
        "query($teamId: String!) { team(id: $teamId) { states { nodes { id name } } } }",
        {"teamId": issue["team"]["id"]},
    )
    states = states_data["team"]["states"]["nodes"]
    review_state = next(
        (s for s in states if s["name"].strip().lower() in REVIEW_STATE_NAMES), None
    )
    if review_state is None:
        print("No 'In Review' workflow state found for this team, skipping state change.")
        return

    gql(
        api_key,
        "mutation($issueId: String!, $stateId: String!) { "
        "issueUpdate(id: $issueId, input: { stateId: $stateId }) { success } }",
        {"issueId": issue["id"], "stateId": review_state["id"]},
    )
    print(f"Moved {issue['identifier']} to '{review_state['name']}'.")


if __name__ == "__main__":
    main()

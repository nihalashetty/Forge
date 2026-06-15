"""Auth, RBAC, and team-management tests (in-process ASGI)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from forge.config import settings
from forge.main import create_app


def _client() -> httpx.AsyncClient:
    app = create_app()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@example.com"


async def test_register_login_me_flow():
    async with _client() as c:
        email = _email()
        r = await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user"]["role"] == "owner"
        token = body["access_token"]

        # duplicate registration is rejected
        assert (await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})).status_code == 400

        # wrong password
        assert (await c.post("/v1/auth/login", json={"email": email, "password": "nope"})).status_code == 401
        # correct password
        r = await c.post("/v1/auth/login", json={"email": email, "password": "supersecret1"})
        assert r.status_code == 200

        # me with token
        r = await c.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["email"] == email


async def test_auth_required_blocks_anonymous(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    async with _client() as c:
        assert (await c.get("/v1/auth/me")).status_code == 401


async def test_rbac_viewer_cannot_manage_team():
    async with _client() as c:
        owner_email, viewer_email = _email(), _email()
        owner = (await c.post("/v1/auth/register", json={"email": owner_email, "password": "ownerpass1"})).json()
        oh = {"Authorization": f"Bearer {owner['access_token']}"}

        # owner invites a viewer with a password so they can log in
        r = await c.post("/v1/team/members", json={"email": viewer_email, "role": "viewer", "password": "viewerpass1"}, headers=oh)
        assert r.status_code == 201, r.text

        viewer = (await c.post("/v1/auth/login", json={"email": viewer_email, "password": "viewerpass1"})).json()
        vh = {"Authorization": f"Bearer {viewer['access_token']}"}

        # viewer is forbidden from listing/managing the team
        assert (await c.get("/v1/team/members", headers=vh)).status_code == 403
        # owner can
        r = await c.get("/v1/team/members", headers=oh)
        assert r.status_code == 200 and len(r.json()) == 2


async def test_invite_without_password_emails_link_and_can_be_accepted():
    async with _client() as c:
        owner_email, invitee_email = _email(), _email()
        owner = (await c.post("/v1/auth/register", json={"email": owner_email, "password": "ownerpass1"})).json()
        oh = {"Authorization": f"Bearer {owner['access_token']}"}

        # invite with no password => a pending 'invited' user. SMTP is unconfigured in tests,
        # so the API hands back a redeemable link instead of emailing it.
        r = await c.post("/v1/team/members", json={"email": invitee_email, "role": "editor"}, headers=oh)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "invited" and body["email_sent"] is False
        assert "invite=" in body.get("invite_url", "")
        token = body["invite_url"].split("invite=", 1)[1]

        # can't log in yet — no password set
        assert (await c.post("/v1/auth/login", json={"email": invitee_email, "password": "whatever1"})).status_code == 401

        # invite-info reflects the pending invite
        info = await c.get(f"/v1/auth/invite-info?token={token}")
        assert info.status_code == 200 and info.json()["email"] == invitee_email

        # redeeming sets the invitee's own password and logs them in
        acc = await c.post("/v1/auth/accept-invite", json={"token": token, "password": "myownpass1"})
        assert acc.status_code == 200, acc.text
        assert acc.json()["user"]["email"] == invitee_email
        assert (await c.post("/v1/auth/login", json={"email": invitee_email, "password": "myownpass1"})).status_code == 200

        # a used invite can't be redeemed again
        assert (await c.post("/v1/auth/accept-invite", json={"token": token, "password": "another123"})).status_code == 400


async def test_refresh_issues_new_access_token():
    async with _client() as c:
        email = _email()
        body = (await c.post("/v1/auth/register", json={"email": email, "password": "supersecret1"})).json()
        r = await c.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200 and "access_token" in r.json()
        # an access token is not accepted as a refresh token
        assert (await c.post("/v1/auth/refresh", json={"refresh_token": body["access_token"]})).status_code == 401


async def test_cannot_demote_only_owner():
    async with _client() as c:
        email = _email()
        body = (await c.post("/v1/auth/register", json={"email": email, "password": "ownerpass1"})).json()
        oh = {"Authorization": f"Bearer {body['access_token']}"}
        uid = body["user"]["id"]
        r = await c.patch(f"/v1/team/members/{uid}", json={"role": "viewer"}, headers=oh)
        assert r.status_code == 400 and "owner" in r.json()["detail"].lower()

# Membership Requests

This document describes the final backend design for group membership requests.

## Schema

The `requests` table stores group invites, join requests, and de-member
requests.

```text
request_id UUID PRIMARY KEY
request_type TEXT NOT NULL
status TEXT NOT NULL DEFAULT 'pending'

sender_sub TEXT NULL REFERENCES users(user_sub) ON DELETE SET NULL
receiver_sub TEXT NULL REFERENCES users(user_sub) ON DELETE SET NULL
created_by_sub TEXT NULL REFERENCES users(user_sub) ON DELETE SET NULL
resolved_by_sub TEXT NULL REFERENCES users(user_sub) ON DELETE SET NULL
group_id UUID NULL REFERENCES groups(group_id) ON DELETE SET NULL

requested_at TIMESTAMPTZ NOT NULL
expires_at TIMESTAMPTZ NOT NULL
resolved_at TIMESTAMPTZ NULL

sender_email_snapshot TEXT NULL
receiver_email_snapshot TEXT NULL
created_by_email_snapshot TEXT NULL
resolved_by_email_snapshot TEXT NULL
group_name_snapshot TEXT NULL
```

New API-created requests should have `created_by_sub` and `group_id`. These
fields are nullable only so request history can survive user/group deletion.
Snapshot fields preserve display context when referenced users or groups are
later deleted.

The lazy expiry query is backed by `idx_requests_status_expires_at` on
`(status, expires_at)`. The main inbox queries are backed by receiver, sender,
creator, and group/status indexes.

## Types And Statuses

Supported `request_type` values:

- `invite`: group admin/admin invites a user to their current group.
- `join_request`: user requests to join a group.
- `demember_request`: group member asks to be removed from their current group.

Supported `status` values:

- `pending`: request can still be acted on.
- `approved`: request was accepted and the membership change was applied.
- `rejected`: request was explicitly declined.
- `expired`: request passed `expires_at` before being resolved.
- `cancelled`: request was withdrawn or invalidated.

All terminal statuses set `resolved_at`. User/admin actions set
`resolved_by_sub`; system-caused transitions, such as expiry or associated
user/group deletion, leave `resolved_by_sub` null.

## Creation APIs

`POST /request/join`

Inputs:

```text
group_id
expires_in_days optional, default 7
```

Rules:

- Requester must exist and must not already belong to a group.
- Target group must exist.
- Duplicate pending join requests for the same user/group are rejected.
- `sender_sub` is the requester, `receiver_sub` is null, and `group_id` is the
  requested group.

`POST /request/invite`

Inputs:

```text
email
expires_in_days optional, default 7
```

Rules:

- Requester must be an admin or group admin and must currently belong to a
  group.
- Invite target is resolved by email.
- Target user must exist and must not already belong to a group.
- Duplicate pending invites for the same user/group are rejected.
- `receiver_sub` is the invited user, `sender_sub` is null, and `group_id` is
  inferred from the requester.

`POST /request/demember`

Inputs:

```text
expires_in_days optional, default 7
```

Rules:

- Requester must currently belong to a group.
- Duplicate pending de-member requests for the same user/group are rejected.
- `sender_sub` is the requester, `receiver_sub` is null, and `group_id` is
  inferred from the requester.

`expires_in_days` is bounded from 1 to 30 days. The backend computes
`expires_at`; clients do not provide raw expiry timestamps.

## Listing APIs

`GET /request/received`

Lists requests where the authenticated user is `receiver_sub`. Default
`status` is `pending`.

`GET /request/sent`

Lists requests where the authenticated user is `sender_sub` or
`created_by_sub`. Default `status` is `pending`.

`GET /group/requests`

Lists requests for the authenticated admin/group admin's current group. Normal
members cannot use this endpoint.

All list endpoints support:

```text
status optional, default pending
request_type optional
recent_days optional, default 30, max 90
limit optional, default 25, max 100
offset optional, default 0
```

`recent_days` applies only to terminal-status queries and filters by
`resolved_at`.

Results are ordered by `requested_at`, newest first. When two requests have
the same `requested_at`, `request_id` keeps pagination stable.

## Serialization

All request responses include:

```text
request_id
status
request_type
requested_at
expires_at
resolved_at
group_id
group_name
```

Admins and group admins for the request group also receive user/audit metadata:

```text
sender_sub
receiver_sub
created_by_sub
resolved_by_sub
sender_name
receiver_name
created_by_name
resolved_by_name
```

Normal users only receive their own `sender_sub` or `receiver_sub` when they are
the sender or receiver. Other users' identifiers and emails are hidden.

## Resolution APIs

`PUT /request/{request_id}/approve`

- Invites are approved by the invited user.
- Join requests are approved by an admin or group admin for the request group.
- De-member requests are approved by an admin or group admin for the request
  group.
- A group admin cannot approve de-membering another group admin.
- Approval revalidates current membership state before applying changes.
- Successful invite or join approval cancels the joined user's other pending
  invites and join requests.
- Successful de-member approval cancels any other pending de-member requests
  for the removed user and group.

`PUT /request/{request_id}/reject`

- Invites are rejected by the invited user.
- Join and de-member requests are rejected by an admin or group admin for the
  request group.

`PUT /request/{request_id}/cancel`

Cancels a pending request when the authenticated user sent it, created it, is an
admin, or is a group admin for the request group.

`DELETE /request/{request_id}`

Compatibility endpoint that cancels the request instead of deleting the row.

## Expiry And Invalid Requests

Before listing or acting on requests, the backend lazily expires pending rows
where `expires_at <= now`.

Requests are also revalidated at resolution time. If a request can no longer be
acted on, for example because the target user joined another group, it is marked
`cancelled`.

Direct membership changes outside the request approval endpoints also clean up
pending request state:

- Directly assigning a user to a group cancels that user's pending invites and
  join requests.
- Directly moving or removing a user from a group cancels pending de-member
  requests for that user and previous group.

When an associated user or group is deleted:

- pending requests become `cancelled`
- already resolved requests keep their existing status
- `resolved_at` is set for newly cancelled pending requests
- `resolved_by_sub` is null for system-caused cancellation
- matching user/group FK fields are nulled
- snapshot fields keep the last known email or group name

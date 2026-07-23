# Ownership and Permissions

This document explains how the backend decides who can access jobs,
structures, users, and groups.

Swagger documents the inputs and outputs of each API endpoint. This document
describes the rules that apply across multiple endpoints.

## Identity, Roles, and Groups

Auth0 tells the backend who the caller is. The local `users` table tells the
backend what that user is allowed to do.

The backend uses three roles:

| Role | Meaning |
|---|---|
| `admin` | Overall administrator. Can manage all users, groups, jobs, and structures. |
| `group_admin` | Administrator for one group. Their extra permissions apply only to their current group. |
| `member` | Regular user. They may or may not belong to a group. |

A `group_admin` must belong to a group. An `admin` does not need to belong to a
group.

## Asset Ownership

In this document, **asset** means either a job or a structure. Both use the same
ownership and permission rules.

An asset has three possible ownership states:

| Ownership | `user_sub` | `group_id` | Meaning |
|---|---|---|---|
| User-owned | Set | Empty | One user owns the asset. |
| Group-owned | Empty | Set | One group owns the asset. |
| Co-owned | Set | Set | A user and a group both own the asset. |

An active asset must always have at least one owner. A soft-deleted asset may
have no owner after its user or group is deleted.

The `is_public` field controls whether regular members of the owning group can
read a group-owned or co-owned asset. It does not make the asset visible to
users outside that group.

## Ownership When an Asset Is Created

The backend sets ownership from the creator's current database record:

- A user without a group creates a user-owned asset.
- A user in a group creates a co-owned asset.
- The client cannot leave out or replace the creator's group ownership.
- Joining a group does not change assets that the user created earlier.

These rules apply to every backend path that creates a job or structure.

## Asset Permission Table

| Action | Overall admin | Direct user owner | Group admin for the owning group | Regular member of the owning group | Other user |
|---|---|---|---|---|---|
| Read a user-owned asset | Yes | Yes | No | No | No |
| Read a private group-owned asset | Yes | Not applicable | Yes | No | No |
| Read a private co-owned asset | Yes | Yes | Yes | No | No |
| Read a public group-owned or co-owned asset | Yes | Yes, when co-owned | Yes | Yes | No |
| Edit or delete an asset | Yes | Yes | Yes | No | No |
| Change visibility of a user-owned asset | Yes | Yes | Not applicable | No | No |
| Change visibility of a group-owned or co-owned asset | Yes | No | Yes | No | No |
| Transfer ownership | Yes | No | Yes, with the restrictions below | No | No |

The categories in this table can overlap. For example, a regular group member
who is also the direct user owner follows the **Direct user owner** column.

## Personal and Group Lists

The personal and group list APIs answer different questions:

- `GET /jobs/` and `GET /structures/` list assets whose `user_sub` is the
  current user.
- Personal lists continue to include co-owned assets after the user leaves the
  group.
- `GET /group/jobs` and `GET /group/structures` list assets whose `group_id`
  is the current user's group.
- A user must currently belong to a group to use a group list.
- Group admins see all assets owned by their group.
- Regular members see only public assets owned by their group.
- A private co-owned asset does not appear in a regular member's group list,
  even when that member is its direct owner. It still appears in their personal
  list.

Regular members can receive the asset's `group_id`. They do not receive another
user owner's `user_sub`. Overall admins, matching group admins, and the direct
user owner can see that field.

## Visibility

For user-owned assets, the direct owner or an overall admin can change
`is_public`. Because the asset has no owning group, this does not give any
additional users access.

For group-owned and co-owned assets:

- An overall admin can change visibility.
- A group admin for the owning group can change visibility.
- The direct user co-owner cannot change visibility unless they are also a
  group admin for that group.
- Making the asset public gives read-only access to current members of the
  owning group.
- Public access never extends to another group or to all authenticated users.

## Ownership Transfers

Jobs and structures use the same ownership transfer rules.

The requested target must have one of these exact shapes:

| Target ownership | `user_sub` | `group_id` |
|---|---|---|
| User-owned | Required | Omitted |
| Group-owned | Omitted | Required |
| Co-owned | Required | Required |

The target user and group must exist.

Overall admins can transfer any asset to a valid user, group, or both.

A group admin has additional restrictions:

- The asset must already be owned by the group admin's current group.
- The asset cannot be transferred to another group.
- A co-owned asset can become user-owned only by keeping its existing user
  co-owner and removing the group owner.
- A co-owned asset can become group-owned by removing its user owner.
- A group-owned asset can become co-owned by adding a user who currently
  belongs to the group.
- A group admin cannot replace an existing user co-owner directly.
- A group-owned asset cannot be transferred directly to user-only ownership.

These restrictions keep group admins from giving group assets to unrelated
users or groups.

## Membership Changes

Group membership and asset ownership are stored separately:

- Joining a group affects ownership of newly created assets only.
- Leaving or being removed from a group does not change existing asset
  ownership.
- A former member keeps access to an asset while they remain its direct user
  owner.
- The former member no longer receives the asset through group list APIs.
- The original group's admins keep their group-owner permissions while the
  asset still has that group's `group_id`.

For example, if Alice creates a job while she belongs to Group A, the job is
co-owned by Alice and Group A. If Alice later leaves, Alice can still access it
as its user owner, and Group A's admins can still manage it as its group owner.

## User and Group Deletion

Deleting an owner keeps the remaining owner's data usable:

- When a user is deleted, their co-owned assets become group-owned.
- When a user is deleted, their user-only assets are soft-deleted.
- When a group is deleted, its co-owned assets become user-owned.
- When a group is deleted, its group-only assets are soft-deleted.

Soft deletion sets `is_deleted` instead of immediately removing the asset row.
Normal asset lookups and lists do not return soft-deleted assets.

Request history cleanup after user or group deletion is described in
[Membership Requests](membership-requests.md).

## Access to Job Files and Results

Permission to read a job also controls access to its related data:

- S3 result files
- Archive downloads
- Cluster result output
- Cluster error output

An endpoint that exposes new job files or results must use the same job read
permission. Checking only that the caller is authenticated is not enough.

## User and Group Management

| Action | Overall admin | Group admin | Regular member |
|---|---|---|---|
| View any user's profile | Yes | No | No |
| View own profile | Yes | Yes | Yes |
| View a profile in the current group | Yes | Yes | No, unless it is their own profile |
| List all users and groups through admin APIs | Yes | No | No |
| List users in the current group | Yes, when the admin belongs to that group | Yes | No |
| Create a group | Yes | No | No |
| Rename a group | Yes | Own group only | No |
| Delete a group | Yes | No | No |
| Change a user's role or group | Yes | No | No |
| Remove a normal member from a group | Yes | Own group only | No |
| Remove another group admin from a group | Yes | No | No |
| Remove themselves from a group | Yes | Yes | No |

Any authenticated user who has a local user record can fetch a group's basic
`group_id` and name when they already know its ID.

Invite, join, and de-member request permissions are described in
[Membership Requests](membership-requests.md).

## Where the Rules Are Implemented

- `models.py` defines the ownership fields and the rule that active assets must
  have an owner.
- `permissions.py` contains shared yes-or-no permission checks.
- `asset_service.py` applies asset permissions, visibility changes, ownership
  transfers, serialization, and shared list behaviour.
- `group_service.py` applies group membership, group asset, and group deletion
  rules.
- `user_service.py` applies user profile, role, group, and user deletion rules.
- Route files load the current local user and call these shared services.

When adding a new asset endpoint:

1. Load the caller's local `User` record.
2. Load a non-deleted job or structure.
3. Use the shared read, write, delete, visibility, or transfer permission.
4. Use the asset's saved `group_id`. Do not infer its owner from the user's
   current group.
5. Apply the same read permission to files, results, or other data belonging to
   the asset.
6. Add tests for the overall admin, direct owner, matching group admin, regular
   group member, and an unrelated user.

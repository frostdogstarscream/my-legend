from __future__ import annotations

from sqlalchemy import text

from ..persistence.models import CharacterProfileRevisionModel, CharacterTemplateModel
from ..workflow.repository import (
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
    WorkflowNotFoundError,
)
from ..workflow.schemas import OperationContext
from .schemas import CharacterImport, CharacterView, RuntimeInput


def advisory(session, key):
    if session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
        )


def protect_canon(old, new):
    for key, value in old.items():
        if key == "character_id" or value is None or value in ("", [], {}):
            continue
        if key in ("immutable_facts", "abilities"):
            if not set(value) <= set(new.get(key, [])):
                raise WorkflowConflictError("CHARACTER_CANON_LOCKED")
        elif new.get(key) != value:
            raise WorkflowConflictError("CHARACTER_CANON_LOCKED")


class CharacterLibrary:
    def __init__(self, session):
        self.session = session

    def row(self, character_id, version=None):
        q = self.session.query(CharacterTemplateModel).filter_by(
            character_id=character_id, status="active"
        )
        if version is not None:
            q = q.filter_by(version=version)
        row = q.order_by(CharacterTemplateModel.version.desc()).first()
        if not row:
            raise WorkflowNotFoundError("CHARACTER_NOT_FOUND")
        return row

    @staticmethod
    def view(row):
        return CharacterView(
            character_id=row.character_id,
            version=row.version,
            canon=row.canon_json,
            runtime_defaults=row.runtime_defaults_json,
            assets=row.assets_json or {},
        )

    def import_character(self, value: CharacterImport):
        advisory(self.session, "character-import:" + value.character_id)
        current = (
            self.session.query(CharacterTemplateModel)
            .filter_by(character_id=value.character_id)
            .order_by(CharacterTemplateModel.version.desc())
            .first()
        )
        canon = value.canon.model_dump(mode="json", exclude_none=True)
        if canon.get("character_id") not in (None, value.character_id):
            raise ValueError("CHARACTER_ID_CONFLICT")
        canon["character_id"] = value.character_id
        runtime = value.runtime_defaults.model_dump(mode="json", exclude_none=True)
        assets = {
            k: v.model_dump(mode="json", exclude_none=True)
            for k, v in value.assets.items()
        }
        if current and (
            current.canon_json,
            current.runtime_defaults_json,
            current.assets_json or {},
        ) == (canon, runtime, assets):
            return self.view(current)
        if value.expected_version != (current.version if current else 0):
            raise WorkflowConflictError("CHARACTER_VERSION_CONFLICT")
        if current:
            protect_canon(current.canon_json, canon)
        row = CharacterTemplateModel(
            character_id=value.character_id,
            version=value.expected_version + 1,
            name=value.canon.name,
            status="active",
            canon_json=canon,
            runtime_defaults_json=runtime,
            assets_json=assets,
        )
        self.session.add(row)
        self.session.flush()
        return self.view(row)

    def update_runtime(self, principal, character_id, expected_revision, patch):
        self.row(character_id)
        advisory(self.session, "character-profile:" + principal + ":" + character_id)
        clean = RuntimeInput.model_validate(patch).model_dump(
            mode="json", exclude_unset=True
        )
        return SqlAlchemyWorkflowRepository(self.session).update_character_profile(
            principal, character_id, expected_revision, clean
        )

    def context(self, request, principal):
        snapshots = {}
        ids = request.characters.selected_character_ids
        if len(ids) != len(set(ids)):
            raise ValueError("CHARACTER_ID_CONFLICT")
        binding_ids = []
        roles = []
        performers = []
        for binding in request.characters.role_bindings:
            cid = binding.get("character_id")
            role = binding.get("story_role_id") or binding.get("role_id")
            if (
                not cid
                or cid not in ids
                or not isinstance(role, str)
                or not role
                or not isinstance(binding.get("performer_id"), str)
                or not binding["performer_id"]
            ):
                raise ValueError("CHARACTER_BINDING_INVALID")
            if (
                role == "narrator"
                or len(role) > 128
                or len(binding["performer_id"]) > 128
            ):
                raise ValueError("CHARACTER_BINDING_INVALID")
            if (
                binding.get("story_role_id")
                and binding.get("role_id")
                and binding["role_id"] != binding["story_role_id"]
            ):
                raise ValueError("CHARACTER_BINDING_INVALID")
            binding["story_role_id"] = role
            binding_ids.append(cid)
            roles.append(role)
            performers.append(binding["performer_id"])
        if len(binding_ids) != len(set(binding_ids)) or len(roles) != len(set(roles)):
            raise ValueError("CHARACTER_BINDING_CONFLICT")
        if ids and set(binding_ids) != set(ids):
            raise ValueError("CHARACTER_BINDING_REQUIRED")
        for cid in ids:
            row = self.row(cid)
            voice = (row.assets_json or {}).get("voice", {})
            bound = next(
                b for b in request.characters.role_bindings if b["character_id"] == cid
            )
            if (
                voice.get("performer_id")
                and voice["performer_id"] != bound["performer_id"]
            ):
                raise ValueError("CHARACTER_PERFORMER_CONFLICT")
            profile = (
                self.session.query(CharacterProfileRevisionModel)
                .filter_by(principal_id=principal, character_id=cid)
                .order_by(CharacterProfileRevisionModel.revision.desc())
                .first()
            )
            RuntimeInput.model_validate(
                request.characters.temporary_profile_overrides.get(cid, {})
            )
            runtime = {
                **row.runtime_defaults_json,
                **(profile.profile_json if profile else {}),
            }
            runtime["asset_refs"] = row.assets_json or {}
            snapshots[cid] = {
                "canon": row.canon_json,
                "canon_revision": row.version,
                "profile": runtime,
                "profile_revision": profile.revision if profile else 0,
            }
        unknown = set(request.characters.temporary_profile_overrides) - set(ids)
        if unknown:
            raise ValueError("CHARACTER_OVERRIDE_NOT_SELECTED")
        return OperationContext(principal_id=principal, character_snapshots=snapshots)

    def check_snapshot_versions(self, plan, selected_ids=None):
        for snapshot in plan.character_snapshots:
            # Historical source snapshots stay usable without forcing a new
            # catalog import or replacing the version that the source froze.
            if selected_ids is not None and snapshot.character_id not in selected_ids:
                continue
            if self.row(snapshot.character_id).version != snapshot.canon_revision:
                raise WorkflowConflictError("CHARACTER_VERSION_CONFLICT")

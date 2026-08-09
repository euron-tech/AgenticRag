"""Document upload, listing, and lifecycle."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, UploadFile

from app.core.deps import AdminDep, CurrentUserDep, require_department
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.pool import admin_conn, rls_conn
from app.ingestion.loaders import SUPPORTED_EXTENSIONS
from app.ingestion.validation import validate_upload
from app.schemas import Document, Ok
from app.services import supabase

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _to_model(row: dict) -> Document:
    return Document(
        id=str(row["id"]),
        department_id=str(row["department_id"]),
        filename=row["filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        status=row["status"],
        error_message=row["error_message"],
        page_count=row["page_count"],
        chunk_count=row["chunk_count"],
        created_at=row["created_at"],
        processed_at=row["processed_at"],
    )


@router.get("/supported-types")
async def supported_types() -> dict[str, list[str]]:
    return {"extensions": SUPPORTED_EXTENSIONS}


@router.get("", response_model=list[Document])
async def list_documents(department_id: str, user: CurrentUserDep) -> list[Document]:
    require_department(user, department_id)
    async with rls_conn(user.id) as conn:
        rows = await repo.list_documents(conn, department_id)
    return [_to_model(r) for r in rows]


@router.post("/upload", response_model=Document, status_code=201)
async def upload_document(
    admin: AdminDep,
    department_id: str = Form(...),
    file: UploadFile = File(...),
) -> Document:
    data = await file.read()
    ext, mime_type, checksum = validate_upload(file.filename or "", data)

    async with admin_conn() as conn:
        department = await repo.get_department(conn, department_id)
        if department is None:
            raise NotFound("Department not found.")

        duplicate = await repo.find_duplicate(
            conn, department_id=department_id, checksum=checksum
        )
        if duplicate:
            raise Conflict(
                f"This exact file is already in the department as "
                f"'{duplicate['filename']}'."
            )

        document_id = uuid.uuid4()
        storage_path = f"{department['slug']}/{document_id}/{file.filename}"
        await supabase.upload(storage_path, data, mime_type)

        row = await conn.fetchrow(
            """
            insert into documents (id, department_id, filename, storage_path, mime_type,
                                   size_bytes, checksum_sha256, uploaded_by)
            values ($1, $2, $3, $4, $5, $6, $7, $8)
            returning id, department_id, filename, mime_type, size_bytes, status,
                      error_message, page_count, chunk_count, created_at, processed_at
            """,
            document_id,
            uuid.UUID(department_id),
            file.filename,
            storage_path,
            mime_type,
            len(data),
            checksum,
            uuid.UUID(admin.id),
        )
        await repo.enqueue_job(conn, str(document_id))
        await repo.write_audit(
            conn,
            actor_id=admin.id,
            action="document.upload",
            entity_type="document",
            entity_id=str(document_id),
            payload={"filename": file.filename, "size_bytes": len(data), "type": ext},
        )

    log.info(
        "document_uploaded",
        extra={"document_id": str(document_id), "department_id": department_id,
               "size_bytes": len(data)},
    )
    return _to_model(dict(row))


@router.get("/{document_id}", response_model=Document)
async def get_document(document_id: str, user: CurrentUserDep) -> Document:
    async with rls_conn(user.id) as conn:
        row = await repo.get_document(conn, document_id)
    if row is None:
        raise NotFound("Document not found.")
    return _to_model(row)


@router.post("/{document_id}/reprocess", response_model=Ok)
async def reprocess(document_id: str, admin: AdminDep) -> Ok:
    async with admin_conn() as conn:
        row = await repo.get_document(conn, document_id)
        if row is None:
            raise NotFound("Document not found.")
        await repo.set_document_status(
            conn, document_id=document_id, status="pending", error_message=None
        )
        await repo.enqueue_job(conn, document_id)
        await repo.write_audit(
            conn,
            actor_id=admin.id,
            action="document.reprocess",
            entity_type="document",
            entity_id=document_id,
        )
    log.info("document_reprocess_queued", extra={"document_id": document_id})
    return Ok()


@router.delete("/{document_id}", response_model=Ok)
async def delete_document(document_id: str, admin: AdminDep) -> Ok:
    async with admin_conn() as conn:
        row = await repo.get_document(conn, document_id)
        if row is None:
            raise NotFound("Document not found.")
        await supabase.remove(row["storage_path"])
        await repo.delete_document(conn, document_id)
        await repo.write_audit(
            conn,
            actor_id=admin.id,
            action="document.delete",
            entity_type="document",
            entity_id=document_id,
            payload={"filename": row["filename"]},
        )
    log.info("document_deleted", extra={"document_id": document_id})
    return Ok()

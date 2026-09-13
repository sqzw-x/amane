from fastapi import APIRouter, HTTPException

from ...utils.model import to_resp
from ..deps import RepoDep
from ..models import CommentCreateRequest, CommentResponse, CommentUpdateRequest

router = APIRouter(tags=["comments"])


@router.post("/metadata/{metadata_id}/comments", status_code=201)
async def create_comment(metadata_id: int, req: CommentCreateRequest, repo: RepoDep) -> CommentResponse:
    comment = await repo.create_comment(metadata_id, req.body)
    if comment is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    return to_resp(CommentResponse, comment)


@router.patch("/comments/{comment_id}")
async def update_comment(comment_id: int, req: CommentUpdateRequest, repo: RepoDep) -> CommentResponse:
    comment = await repo.update_comment(comment_id, body=req.body)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return to_resp(CommentResponse, comment)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: int, repo: RepoDep) -> None:
    deleted = await repo.delete_comment(comment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Comment not found")

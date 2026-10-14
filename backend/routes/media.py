"""Download dos arquivos de mídia das conversas.

Servido pelo backend (e não como pasta estática) justamente para passar pelo
`get_current_user` e pelo filtro de `tenant_id`: o arquivo de uma empresa não
pode ser aberto por outra, mesmo com o id em mãos. Como o JWT vem em cookie
httpOnly, `<img src>` / `<video src>` no chat já chegam autenticados.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import media_store
from auth import get_current_user
from db import get_db

router = APIRouter(prefix="/api/media", tags=["media"])

# Imagem, vídeo e áudio abrem no próprio chat; documento é para baixar.
INLINE_KINDS = {"image", "video", "audio"}


@router.get("/{media_id}")
async def download(media_id: str, user: dict = Depends(get_current_user)):
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    db = get_db()
    doc = await db.media_files.find_one(
        {"id": media_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    if doc.get("purged_at"):
        # 410, não 404: o arquivo existiu e foi removido pela retenção. O chat
        # já mostra "mídia expirada" pelo meta da mensagem; isto cobre uma aba
        # aberta desde antes da varredura.
        raise HTTPException(status_code=410, detail="Mídia expirada e removida")
    path = media_store.file_path(doc)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    disposition = "inline" if doc["kind"] in INLINE_KINDS else "attachment"
    return FileResponse(
        path,
        media_type=doc["mime"],
        filename=doc["filename"],
        content_disposition_type=disposition,
        headers={
            "Cache-Control": "private, max-age=86400",
            # Defesa em profundidade do ZZ-07: `nosniff` impede o navegador de
            # reinterpretar o tipo (um "documento" que ele adivinhe como HTML),
            # e a CSP `sandbox` desliga script/plugins caso algo ainda renderize
            # inline. Com o SVG já classificado como documento (anexo), estes
            # cabeçalhos fecham o resto do caminho para qualquer tipo servido.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )

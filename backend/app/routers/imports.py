"""Importação de extratos (C6 PDF, OFX, Nubank CSV, CSV genérico) e de planilhas do FinFam."""
import io
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.deps import Ctx, get_ctx
from app.services import import_service as svc

router = APIRouter(prefix='/imports', tags=['imports'])

MAX_UPLOAD = 5 * 1024 * 1024


class ImportItem(BaseModel):
    user_id: int
    day: int = Field(ge=1, le=31)
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    category: str = Field('Outros', max_length=50)
    payment_method: Literal['PIX', 'Cartão de Débito', 'Cartão de Crédito', 'Dinheiro', 'VR', 'VA'] = 'PIX'
    bank: str | None = Field(None, max_length=60)
    create_rule: bool = Field(False, description='Criar regra de categoria a partir da descrição')


class ConfirmIn(BaseModel):
    items: list[ImportItem] = Field(min_length=1, max_length=2000)


async def _read(upload: UploadFile) -> bytes:
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=422, detail='Selecione um arquivo.')
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail='Arquivo maior que 5 MB.')
    return content


@router.get('/formats', summary='Formatos suportados')
def formats():
    return [{'id': k, 'label': v} for k, v in svc.FORMATS.items()]


@router.post('/preview', summary='Lê o arquivo e devolve as transações com categoria sugerida e duplicatas')
async def preview(file: UploadFile = File(...), format: str = Form(...), user_id: int = Form(...),
                  bank: str | None = Form(None), pdf_password: str | None = Form(None),
                  ctx: Ctx = Depends(get_ctx)):
    member = ctx.member(user_id)
    content = await _read(file)
    try:
        result = svc.preview(format, content, member.id, ctx.uids, ctx.tenant_id, bank, pdf_password)
    except svc.ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not result['items']:
        raise HTTPException(status_code=422, detail='Nenhuma transação de saída encontrada. '
                                                    'Verifique o arquivo e o formato selecionado.')
    return result


@router.post('/confirm', summary='Grava as transações confirmadas')
def confirm(body: ConfirmIn, ctx: Ctx = Depends(get_ctx)):
    items = [{**i.model_dump(), 'amount': float(i.amount)} for i in body.items]
    result = svc.confirm(items, ctx.uids, ctx.tenant_id)
    msg = f"{result['imported']} despesa(s) importada(s) com sucesso!"
    if result['rules_created']:
        msg += f" {result['rules_created']} regra(s) de categorização criada(s)."
    return {**result, 'message': msg}


@router.post('/finfam-xlsx', summary='Importa um .xlsx exportado pelo FinFam')
async def import_xlsx(file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    if not (file.filename or '').lower().endswith('.xlsx'):
        raise HTTPException(status_code=422, detail='O arquivo deve ser um .xlsx exportado pelo FinFam.')
    content = await _read(file)
    try:
        result = svc.import_finfam_xlsx(io.BytesIO(content), ctx.members)
    except svc.ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    msg = (f"{result['imported']} despesa(s) importada(s). {result['skipped']} linha(s) ignorada(s)."
           if result['imported'] else
           f"Nenhuma despesa importada. {result['skipped']} linha(s) ignorada(s). "
           'Verifique se os nomes dos membros correspondem aos da família.')
    return {**result, 'message': msg}

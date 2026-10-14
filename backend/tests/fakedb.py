"""Mongo falso, o suficiente para exercitar o codigo real do backend.
Nao vai para o repo — e andaime de teste, porque o Mongo real esta atras de
tunel SSH e nao esta no ar nesta maquina.
"""
import copy
import itertools
import re as _re


def _get(doc, path):
    cur = doc
    for parte in path.split("."):
        if not isinstance(cur, dict) or parte not in cur:
            return None
        cur = cur[parte]
    return cur


def _set(doc, path, valor):
    partes = path.split(".")
    cur = doc
    for p in partes[:-1]:
        cur = cur.setdefault(p, {})
    cur[partes[-1]] = valor


def _casa_valor(valor, cond):
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
        for op, alvo in cond.items():
            if op == "$ne" and valor == alvo:
                return False
            elif op == "$in" and valor not in (alvo or []):
                if not (isinstance(valor, list) and set(valor) & set(alvo or [])):
                    return False
            elif op == "$nin" and valor in (alvo or []):
                return False
            elif op == "$lt" and not (valor is not None and valor < alvo):
                return False
            elif op == "$lte" and not (valor is not None and valor <= alvo):
                return False
            elif op == "$gte" and not (valor is not None and valor >= alvo):
                return False
            elif op == "$gt" and not (valor is not None and valor > alvo):
                return False
            elif op == "$regex":
                if valor is None or not _re.search(alvo, str(valor),
                                                   _re.I if "i" in cond.get("$options", "") else 0):
                    return False
            elif op == "$exists" and (valor is not None) != alvo:
                return False
        return True
    if isinstance(valor, list):
        return cond in valor
    return valor == cond


def _casa(doc, filtro):
    for chave, cond in (filtro or {}).items():
        if chave == "$or":
            if not any(_casa(doc, sub) for sub in cond):
                return False
        elif chave == "$and":
            if not all(_casa(doc, sub) for sub in cond):
                return False
        elif chave.startswith("$"):
            raise NotImplementedError(chave)
        elif not _casa_valor(_get(doc, chave), cond):
            return False
    return True


def _projeta(doc, projection):
    d = copy.deepcopy(doc)
    if not projection:
        d.pop("_id", None)
        return d
    incluir = {k for k, v in projection.items() if v and k != "_id"}
    if incluir:
        out = {k: d[k] for k in incluir if k in d}
        if projection.get("_id"):
            out["_id"] = d.get("_id")
        return out
    for k, v in projection.items():
        if not v:
            d.pop(k, None)
    return d


def _expr(doc, e):
    """Avalia expressao de agregacao ($lt, $and, referencia de campo, literal)."""
    if isinstance(e, str) and e.startswith("$"):
        return _get(doc, e.lstrip("$"))
    if not isinstance(e, dict):
        return e
    for op, args in e.items():
        vals = [_expr(doc, a) for a in args] if isinstance(args, list) else _expr(doc, args)
        if op == "$and":
            return all(vals)
        if op == "$or":
            return any(vals)
        if op == "$not":
            return not (vals[0] if isinstance(vals, list) else vals)
        if op in ("$lt", "$lte", "$gt", "$gte", "$eq", "$ne"):
            a, b = vals
            if a is None or b is None:
                return (a == b) if op == "$eq" else (a != b) if op == "$ne" else False
            return {"$lt": a < b, "$lte": a <= b, "$gt": a > b,
                    "$gte": a >= b, "$eq": a == b, "$ne": a != b}[op]
        if op == "$substr":
            texto, inicio, tamanho = vals
            return (str(texto) if texto is not None else "")[inicio:inicio + tamanho]
        if op == "$cond":
            cond, entao, senao = args
            return entao if _expr(doc, cond) else senao
        raise NotImplementedError(op)
    return None


class _Res:
    def __init__(self, matched=0, modified=0):
        self.matched_count = matched
        self.modified_count = modified
        self.deleted_count = modified


class _Cursor:
    """Ordena sobre o documento cru e projeta so na saida — é a ordem do Mongo
    real, e é o que permite desempatar por `_id` com projecao {"_id": 0}."""

    def __init__(self, docs, projection=None, bruto=False):
        self._docs = docs
        self._proj = projection
        # `bruto` e o resultado de aggregate: ali o `_id` e a chave do grupo,
        # nao o id do documento, e projetar sobre ele apagaria o resultado.
        self._bruto = bruto

    def sort(self, chave, direcao=1):
        pares = chave if isinstance(chave, list) else [(chave, direcao)]
        for campo, d in reversed(pares):
            self._docs.sort(key=lambda x: (_get(x, campo) is None, _get(x, campo) or 0),
                            reverse=(d < 0))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n=None):
        saida = self._docs[:n] if n else list(self._docs)
        return saida if self._bruto else [_projeta(d, self._proj) for d in saida]

    def __aiter__(self):
        async def gen():
            for d in list(self._docs):
                yield d if self._bruto else _projeta(d, self._proj)
        return gen()


class Colecao:
    _seq = itertools.count(1)

    def __init__(self, nome):
        self.nome = nome
        self.docs = []

    async def insert_one(self, doc):
        doc.setdefault("_id", next(self._seq))
        self.docs.append(copy.deepcopy(doc))
        return _Res(1, 1)

    async def insert_many(self, docs):
        for d in docs:
            await self.insert_one(d)
        return _Res(len(docs), len(docs))

    async def find_one(self, filtro=None, projection=None):
        for d in self.docs:
            if _casa(d, filtro):
                return _projeta(d, projection)
        return None

    def find(self, filtro=None, projection=None):
        return _Cursor([d for d in self.docs if _casa(d, filtro)], projection)

    async def count_documents(self, filtro=None):
        return sum(1 for d in self.docs if _casa(d, filtro))

    def _aplica(self, doc, update, inserindo=False):
        for op, campos in update.items():
            if op == "$set":
                for k, v in campos.items():
                    _set(doc, k, v)
            elif op == "$setOnInsert":
                # So vale quando o upsert de fato criou o documento; num
                # documento que ja existia o Mongo ignora este operador.
                if inserindo:
                    for k, v in campos.items():
                        _set(doc, k, v)
            elif op == "$inc":
                for k, v in campos.items():
                    _set(doc, k, (_get(doc, k) or 0) + v)
            elif op == "$unset":
                for k in campos:
                    partes = k.split(".")
                    cur = doc
                    for p in partes[:-1]:
                        cur = cur.get(p) if isinstance(cur, dict) else None
                        if cur is None:
                            break
                    if isinstance(cur, dict):
                        cur.pop(partes[-1], None)
            elif op == "$push":
                # Campo ausente vira lista com um item, igual ao Mongo.
                for k, v in campos.items():
                    _set(doc, k, list(_get(doc, k) or []) + [v])
            elif op == "$pull":
                # So a forma usada no projeto: `{"campo": {"chave": valor}}`,
                # que remove os itens do array cujos campos casam todos.
                for k, cond in campos.items():
                    atual = _get(doc, k) or []
                    if isinstance(cond, dict):
                        _set(doc, k, [item for item in atual
                                      if not (isinstance(item, dict)
                                              and all(item.get(ck) == cv for ck, cv in cond.items()))])
                    else:
                        _set(doc, k, [item for item in atual if item != cond])
            else:
                raise NotImplementedError(op)

    async def update_one(self, filtro, update, upsert=False, **kw):
        for d in self.docs:
            if _casa(d, filtro):
                self._aplica(d, update)
                return _Res(1, 1)
        if upsert:
            # O Mongo semeia o documento novo com os campos de igualdade do
            # filtro e so depois aplica o update. Sem isto, todo codigo que usa
            # upsert (contador de consumo de IA, conexao de IA) parecia nao
            # gravar nada no teste, sem erro nenhum.
            doc = {k: v for k, v in (filtro or {}).items()
                   if not k.startswith("$") and not isinstance(v, dict)}
            self._aplica(doc, update, inserindo=True)
            self.docs.append(doc)
            return _Res(0, 1)
        return _Res(0, 0)

    async def update_many(self, filtro, update, **kw):
        n = 0
        for d in self.docs:
            if _casa(d, filtro):
                self._aplica(d, update)
                n += 1
        return _Res(n, n)

    async def find_one_and_update(self, filtro, update, sort=None, projection=None,
                                  return_document=None, **kw):
        candidatos = [d for d in self.docs if _casa(d, filtro)]
        if sort:
            for campo, direcao in reversed(sort):
                candidatos.sort(key=lambda x: _get(x, campo) or 0, reverse=(direcao < 0))
        if not candidatos:
            return None
        d = candidatos[0]
        self._aplica(d, update)
        return _projeta(d, projection)

    async def delete_one(self, filtro):
        for i, d in enumerate(self.docs):
            if _casa(d, filtro):
                self.docs.pop(i)
                return _Res(1, 1)
        return _Res(0, 0)

    async def delete_many(self, filtro):
        antes = len(self.docs)
        self.docs = [d for d in self.docs if not _casa(d, filtro)]
        return _Res(antes - len(self.docs), antes - len(self.docs))

    async def bulk_write(self, ops, ordered=True):
        for op in ops:
            nome = type(op).__name__
            if nome == "InsertOne":
                await self.insert_one(dict(op._doc))
            elif nome == "UpdateOne":
                await self.update_one(op._filter, op._doc)
            elif nome == "DeleteOne":
                await self.delete_one(op._filter)
            else:
                raise NotImplementedError(nome)
        return _Res(len(ops), len(ops))

    async def distinct(self, campo, filtro=None):
        vistos = []
        for d in self.docs:
            if not _casa(d, filtro):
                continue
            v = _get(d, campo)
            for item in (v if isinstance(v, list) else [v]):
                if item is not None and item not in vistos:
                    vistos.append(item)
        return vistos

    def aggregate(self, pipeline):
        docs = [d for d in self.docs]
        for etapa in pipeline:
            if "$match" in etapa:
                docs = [d for d in docs if _casa(d, etapa["$match"])]
            elif "$group" in etapa:
                g = etapa["$group"]
                chave_expr = g["_id"]
                composto = (isinstance(chave_expr, dict)
                            and not any(k.startswith("$") for k in chave_expr))
                grupos = {}
                for d in docs:
                    if chave_expr is None:
                        chave = None
                    elif composto:
                        # Chave composta vira tupla para poder indexar o dict, e
                        # volta a dicionario na saida, como faz o Mongo.
                        chave = tuple(sorted(
                            (k, _expr(d, v)) for k, v in chave_expr.items()))
                    else:
                        chave = _expr(d, chave_expr)
                    alvo = grupos.setdefault(chave, {"_id": chave})
                    for saida, expr in g.items():
                        if saida == "_id":
                            continue
                        if "$sum" in expr:
                            v = expr["$sum"]
                            if isinstance(v, dict):
                                inc = _expr(d, v) or 0
                            elif isinstance(v, str) and v.startswith("$"):
                                inc = _get(d, v.lstrip("$")) or 0
                            else:
                                inc = v
                            alvo[saida] = alvo.get(saida, 0) + inc
                        elif "$push" in expr:
                            alvo.setdefault(saida, []).append(_expr(d, expr["$push"]))
                        elif "$avg" in expr:
                            val = _get(d, expr["$avg"].lstrip("$"))
                            if val is not None:
                                acc = alvo.setdefault("__avg__" + saida, [])
                                acc.append(val)
                for g_doc in grupos.values():
                    if isinstance(g_doc.get("_id"), tuple):
                        g_doc["_id"] = dict(g_doc["_id"])
                    for chave in [k for k in g_doc if k.startswith("__avg__")]:
                        vals = g_doc.pop(chave)
                        g_doc[chave[7:]] = sum(vals) / len(vals) if vals else None
                docs = list(grupos.values())
            else:
                raise NotImplementedError(etapa)
        return _Cursor(docs, bruto=True)


class FakeDB:
    def __init__(self):
        self._cols = {}

    def __getattr__(self, nome):
        if nome.startswith("_"):
            raise AttributeError(nome)
        return self._cols.setdefault(nome, Colecao(nome))

    def __getitem__(self, nome):
        return self._cols.setdefault(nome, Colecao(nome))

---
description: Aplica a filosofia Unix ao código do projeto — funções pequenas com uma única responsabilidade, composição em vez de duplicação, sem complexidade desnecessária
disable-model-invocation: false
argument-hint: "[arquivo ou módulo opcional]"
---

## Revisão Unix Philosophy — FinFam

Você é um engenheiro que segue rigorosamente a filosofia Unix aplicada a Python/FastAPI e React:

> *"Faça uma coisa e faça bem. Componha em vez de duplicar. Prefira simples a esperto."*

### Escopo

Se `$ARGUMENTS` foi passado, analise apenas esse arquivo ou módulo.
Caso contrário, analise todos os arquivos em `backend/app/routers/`, `backend/app/services/` e `frontend/src/`.

---

### Princípios a verificar

#### 1. Responsabilidade Única
Cada função/rota deve fazer **uma** coisa. Sinais de violação:
- Funções com mais de 30 linhas
- Rotas que fazem lógica de negócio pesada inline (ex: cálculos de parcelas dentro da rota)
- Uma função que busca dados E transforma E salva E redireciona tudo junto

#### 2. Sem Duplicação (DRY)
Identifique blocos de código repetidos que poderiam virar um helper:
- Filtros de tenant (`Expense.user_id.in_(uids)`) repetidos em vários lugares quando um helper já existe
- Padrões `if not session.get('logged_in'): redirect(...)` duplicados
- Cálculos de mês/ano repetidos

#### 3. Composição em vez de Herança/Condicionais complexas
- `if/elif` longos que poderiam ser um dicionário de dispatch
- Lógica condicional que poderia ser extraída em funções nomeadas

#### 4. Sem Complexidade Desnecessária
- Variáveis intermediárias que existem só para ser retornadas imediatamente
- Comentários que explicam O QUÊ (o código já diz) em vez do POR QUÊ
- Imports não usados

#### 5. Nomes que Comunicam Intenção
- Variáveis de uma letra fora de loops curtos
- Nomes genéricos como `data`, `result`, `temp`, `obj`
- Funções prefixadas com `do_`, `handle_`, `process_` sem especificidade

---

### Saída esperada

Para cada problema encontrado, mostre:

```
[arquivo:linha] Princípio violado
  Problema: descrição concisa
  Sugestão: como corrigir (com snippet se necessário)
```

Ao final, liste as **3 melhorias de maior impacto** em ordem de prioridade e pergunte se deseja aplicá-las.

Não refatore nada sem confirmação explícita do usuário.

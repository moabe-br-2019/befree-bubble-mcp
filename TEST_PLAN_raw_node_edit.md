# Plano de teste — feat/raw-node-edit-primitives

Branch sob teste: `feat/raw-node-edit-primitives` @5e70624 (repo `DEV/be-free-mcp`).

## Pré-requisito (obrigatório)

O `~/.claude.json` foi repontado: o MCP `befree-bubble-mcp` agora roda do venv de
`DEV/be-free-mcp` (install editável, o código da branch é executado direto).

Antes: `DEV/auton/befree-bubble-mcp/.venv/Scripts/python.exe`
Agora: `DEV/be-free-mcp/.venv/Scripts/python.exe`

**Cada sessão do Claude Code precisa ser reiniciada** para subir o servidor novo. Uma
sessão já aberta continua falando com o processo antigo e não vai enxergar
`bubble_live_node_read` nem `bubble_node_edit`.

Checagem rápida após reiniciar: as tools `bubble_live_node_read`, `bubble_node_edit` e
`verify_write` devem estar disponíveis. Se não aparecerem, o servidor antigo ainda está no ar.

Regressão conhecida: o commit `ab6e468` ("merge every context root when listing workflows")
está só na branch `fix/workflow-root-overlay-shadowing` e **não** está nesta branch. Se o
listar-workflows vier incompleto em app com múltiplos roots de contexto, é essa ausência,
não um bug novo.

## Perfis disponíveis

- `auto-on` — app `auto-on`, versão `live`
- `mcp-test` — app `mcp-test-app`, versão `test`

Testes destrutivos só em `mcp-test`. Em `auto-on` rodar apenas leitura e dry-run
(`execute=false`).

## 1. `bubble_live_node_read`

Lê um node cru do editor Bubble em execução.

- **1.1 leitura feliz** — pointer para uma action existente, ex.
  `["api","<wf_id>","actions","0"]`. Esperado: corpo cru na forma codificada (não a forma
  traduzida do export), sem tradução de root key.
- **1.2 pointer de container** — pointer para o mapa de actions
  `["api","<wf_id>","actions"]`. Deve devolver o mapa, e escrita nesse pointer deve ser
  recusada (ver 2.4).
- **1.3 pointer inexistente** — resultado estruturado de erro, nunca exceção crua.
- **1.4 `read_timeout_sec` inválido** — `0`, `-5`, `"abc"`. Esperado
  `{"ok": false, "error": "invalid_read_timeout_sec"}` nos três casos.
- **1.5 readiness/retry** — disparar a leitura com o editor ainda carregando. Deve
  reinicializar a página e tentar de novo em vez de falhar direto; a detecção de
  `NotReadyError` é estrutural, não por texto de mensagem.
- **1.6 identidade do app** — a verificação de app_id acontece na mesma avaliação da
  leitura. Passar `app_id` que não bate com o editor aberto deve falhar de forma explícita,
  não devolver o node do app errado.
- **1.7 perfil deslogado** — deve ser detectado e reportado como tal.

## 2. `bubble_node_edit`

Ciclo read-patch-write-verify sobre um node vivo.

- **2.1 `op="patch"` dry-run** — `execute=false`, pointer para UMA action
  (`["api","<wf_id>","actions","3"]`), `leaf_pointer` para uma folha. Nada deve ser escrito;
  o payload previsto deve aparecer no resultado.
- **2.2 `op="patch"` execute** — mesmo caso com `execute=true` em `mcp-test`. Esperado:
  escrita + re-leitura provando que o valor mudou. O resultado carrega `session_id` e a
  forma de intent SetData/Update com índice coerente.
- **2.3 `op="reorder"`** — pointer no mapa de actions, `order` com todas as chaves
  existentes exatamente uma vez. Deve renumerar o mapa e repontar `_index.id_to_path`.
- **2.4 `reorder` que perde passo** — `order` faltando uma chave, ou com chave repetida.
  Deve ser recusado, não escrever.
- **2.5 pointer de container em patch** — deve ser recusado (patch em container é proibido).
- **2.6 corpo do node** — todo node escrito passa por guard; body malformado é recusado
  antes da escrita.
- **2.7 overlay de mutação** — após um `execute` bem-sucedido, o overlay é gravado com o
  `app_id` correto (fallback de `app_id` no overlay foi corrigido).

## 3. Verificação pós-escrita / `verify_write`

`/appeditor/write` devolve HTTP 200 para qualquer corpo, então o 200 não prova nada.

- **3.1 verificação universal** — tools do aria-runtime devem re-ler e provar que a escrita
  entrou. Cobrir pelo menos `add_action` e `log_the_user_in`, onde havia três defeitos de
  escrita silenciosa.
- **3.2 `verify` é control arg** — `verify` não pode vazar para o payload da tool; é
  argumento de controle do runtime.
- **3.3 falso negativo** — escrita que de fato entrou não pode ser reportada como falha.
- **3.4 evidência preservada** — em falha de verificação, o corpo lido e o payload enviado
  devem sobreviver no resultado para diagnóstico (antes eram destruídos).
- **3.5 `verify_write` direto** — chamar a tool isolada sobre uma escrita conhecida boa e
  uma conhecida ruim.

## 3B. Elementos dentro de reusables (`%ed`)

O plano original só exercitava nodes sob `api`, e por isso não pegou que
`bubble_live_node_read` abria o editor sempre em `page?name=index`. A hipótese por trás da
correção é que a página do editor decide quais subárvores carregam, e que a subárvore de um
reusable nunca começa a carregar na `index`. A página passou a ser derivada do próprio pointer
(`%ed.<key>` abre a página daquele reusable, `%p3.<key>` abre aquela página).

Diagnóstico original: app `orana-digital-system`, branch `test`, reusable **New Client Form**
(key `bTZyj`, id `bTZyf`). Execução de 2026-09-04: app `mcp-test-app`, branch `test`, perfil
`mcp-test`, com fixtures criados para o teste — reusable `MCP Reusable Form` (key `bp5R4`),
input `in_mandatory_probe` (key `bsHMF`, id `beghi`) e style `MCP Probe Style`
(`Button_mcp_probe_style_`).

**A hipótese da causa raiz não se reproduziu no `mcp-test-app`.** Forçando a URL antiga, fixa
em `name=index`, tanto `["%p3","bICcQ"]` (página `test_mcp_new`) quanto `["%ed","bp5R4"]`
leram normalmente. Nesse app a `index` prime as duas subárvores, então a correção não muda
resultado nenhum aqui e continua sem evidência contra o bug que ela mira. O caso da Orana
segue sendo o único onde a falha foi observada, e é nele que 3B.1/3B.2 precisam rodar. A
afirmação do docstring de `editor_page.py` de que uma página fora da `index` também nunca
chega está contrariada pela medição.

- **3B.1 leitura rasa do reusable** — `bubble_live_node_read` com `pointer=["%ed","<key>"]`.
  Esperado: o node, não `pointer_not_ready`. Passou no `mcp-test-app` (`bp5R4`), mas também
  passava com a URL antiga; só tem valor diagnóstico rodando na Orana (`bTZyj`).
- **3B.2 leitura profunda** — na Orana,
  `pointer=["%ed","bTZyj","%el","bTaCn","%el","bTZse0","%el","bTTWc"]`. Este é o pointer que
  falhava três vezes seguidas, inclusive com `read_timeout_sec=120`. **Não executado.**
- **3B.3 nome do reusable não resolvido** — apagar/renomear o cache do perfil e repetir 3B.1.
  Esperado: falhar citando a página `name=<key>` que não existe, e não expirar 90s em
  `pointer_not_ready`. O cache correto é o índice em
  `contexts/<perfil>/bubble_modules/<app>/element_definitions/__index.json`. Executado com o
  índice sem a entrada: a leitura caiu no fallback `name=bp5R4` e **mesmo assim leu**, o que
  confirma que nesse app a página escolhida é irrelevante. Fica em aberto se, num app onde a
  página importa, esse fallback piora o resultado — ele troca uma página que funcionava
  (`index`) por uma que não existe.
- **3B.4 página que não é a index** — pointer `["%p3","<key de outra página>", ...]`. Leu, e
  leu também com a URL antiga forçada: **não distingue nada**.
- **3B.5 lote com páginas diferentes** — ler, na mesma chamada, um pointer de reusable, um de
  outra página e um de `api`. Esperado: um único browser aberto, uma navegação por página
  distinta, e um resultado por pointer. **Não executado.**
- **3B.6 sessão expirada** — com o perfil deslogado, qualquer pointer deve reportar
  `not_logged_in` apontando `bubble_session_login`, e **não** `pointer_not_ready`. O editor
  serve o app pedido por ~1s e então é jogado para `https://bubble.io/`, onde `appquery`
  responde pelo app `meta` da própria Bubble. Passou.
- **3B.7 `bubble_node_edit` em elemento de reusable** — `op="patch"` em
  `["%ed","<key>","%el","<key>"]` com `leaf_pointer=["%p"]` levando `mandatory` e `%1m` a
  `false`. Passou: `verified: true`, `divergence: null`. A outra metade — `op="remove"` na
  chave de `states` cuja condição religa `mandatory` — **não foi executada**, porque o fixture
  do `mcp-test-app` não tem condição de elemento; na Orana os dois lugares precisam ser
  mexidos, já que só a propriedade base não resolve.
- **3B.8 `mandatory` nas tools de alto nível** — `update_input`/`update_dropdown` com
  `mandatory` (alias de `required`). Passou: escreveu `%p.mandatory` e `%p.%1m`, verificado.
  Um argumento que a tool não declara e que não produziu nenhuma escrita volta em
  `unrecognized_arguments` — testado com `obrigatorio=true`, que retornou a lista e o aviso em
  vez de sumir dentro de "No update fields were provided". Passou.
- **3B.9 `delete_style_condition`** — remover uma condição de um style e reler o style.
  Executado contra o editor real: a remoção escreve `%s` inteiro e a releitura confirma que a
  condição sumiu e que o resto do style ficou intacto. Ressalva: removendo a **última**
  condição, o Bubble descarta o mapa vazio em vez de guardar `{}`, e o `write_verification`
  reporta `verified: false` com `expected present / actual absent` em `styles.<id>.%s`. A
  escrita está certa; o verificador é que não espera o mapa sumir.

### Achados colaterais da execução de 2026-09-04

- `create_reusable` cria um reusable **sem nome, sem id e sem tipo**. O `CreateElement` manda
  `%x`, `id` e `%nm` no body, mas só os `SetData` seguintes persistem: o export mostra
  `element_definitions.bp5R4` apenas com `elements` e `properties`. É por isso que o índice
  gravou `"bp5R4": "CustomDefinition:bp5R4"`, com o nome igual à key, e que
  `editor_page.resolve_context_name` não tem nome para resolver. Comportamento pré-existente,
  mas derruba na prática a resolução de nome introduzida aqui.
- `refresh_profile_cache` está quebrado: `Refresh script not found:
  src/bubble_mcp/aria_runtime/scripts/refresh_profile.py`. O arquivo não existe no
  repositório, nem neste branch nem na `main`. O caminho que funciona é
  `bubble_profile_cache_refresh`. Isso importa para 3B.3, que depende de reconstruir o índice
  do perfil.
- `editor_page` lê só o `__index.json` e o export `.bubble`, e ignora o mutation overlay. Um
  reusable criado pelo próprio MCP fica invisível para a resolução de nome até um refresh
  completo do cache.

## 4. Resolução de contexto (key vs id)

- **4.1 resolver de contexto** — deve preferir path/key real do node ao `bubble_id`.
- **4.2 page key** — derivada do path real do editor da página crawleada, não do id dela.
- **4.3 workflow por key** — resolução endereçada por key confia no chamador; não deve ser
  sobrescrita pelo discovery.
- **4.4 contexto do perfil** — quando nenhum contexto é passado, carrega o do perfil.

## Como reportar

Para cada item: id do teste, tool chamada, argumentos, resultado (ok/erro), e se bateu com o
esperado. Falha: colar a linha decisiva do erro e o pointer usado. Não colar dump inteiro.

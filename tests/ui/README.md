# Testes de UI contra um app Bubble real

Estes testes abrem o app no navegador e conferem o que o usuário vê. Não fazem parte da suíte
unitária: dependem de rede, do app no ar e das credenciais do preview. Sem qualquer uma dessas
coisas eles se marcam como `skipped`, então `pytest` continua verde numa máquina sem nada
configurado.

## Por que existe ID nos elementos

Um seletor de Playwright só é estável se apontar para um `id` que você controla. Sem isso ele
cai em texto e nome de classe gerado pelo Bubble, que muda a cada deploy — o teste passa uma
vez e apodrece.

Duas coisas precisam ser verdade no app, e as duas são escrita via MCP:

| o quê | como |
|---|---|
| opção de expor ID ligada no app | `set_project_setting` com o alias `advanced-expose-id-option` |
| ID em cada elemento sob teste | `html_id` nas tools de elemento (`update_button`, `update_text_element`, ...), que grava `%p.unique_id` |

O `test_exposed_ids.py` existe justamente para falhar primeiro quando uma dessas duas deixa de
valer — um savepoint restaurado, um merge de branch, alguém desligando o setting. Sem ele, a
quebra apareceria como "o botão sumiu" em todos os outros testes ao mesmo tempo.

## Rodar

O app com proteção de senha no preview responde HTTP Basic (`401 WWW-Authenticate: Basic`), por
isso o contexto usa `http_credentials`. As credenciais vêm do ambiente e não ficam em arquivo
nenhum do repositório.

Essa proteção vem **ligada por padrão em apps do plano agency**, e o dono de um app em qualquer
plano pode ligá-la para manter estranhos fora de uma versão em andamento. O Bubble a preenche
com o par literal `username` / `password`, e é comum o dono deixar assim — é uma parede contra
quem passa, não um segredo. Então um 401 aqui normalmente significa ambiente não configurado, e
não par desconhecido: tente o padrão, ou pergunte ao dono do app. Isso protege a versão de
preview, não as contas de usuário do próprio app.

```bash
export BUBBLE_PREVIEW_USER=...
export BUBBLE_PREVIEW_PW=...
python -m pytest tests/ui -q
```

Use o Python do `.venv` do projeto: os binários do Chromium são instalados por interpretador, e
um `playwright install` rodado em outro Python não vale para este. Se faltar, o teste avisa com
o comando exato.

Variáveis reconhecidas:

**Nenhum app é padrão aqui.** Estes testes são distribuídos junto com o servidor, então um
padrão apontando para um app específico mandaria toda instalação alheia contra um app do Bubble
que ela não consegue abrir — e o erro pareceria teste quebrado, não falta de configuração. Tudo
vem do ambiente, e o que faltar vira `skipped` dizendo qual variável setar.

| variável | padrão | efeito |
|---|---|---|
| `BUBBLE_UI_APP_ID` | — | app alvo; sem ele, skip |
| `BUBBLE_UI_PROFILE` | — | perfil bubble-mcp cuja sessão de editor cunha a impersonação; sem ele, skip |
| `BUBBLE_UI_PAGE` | — | página a abrir; sem ela, skip |
| `BUBBLE_UI_ELEMENT_IDS` | — | ids esperados no DOM, separados por vírgula; sem eles, skip |
| `BUBBLE_PREVIEW_USER` / `BUBBLE_PREVIEW_PW` | — | credenciais do Basic; sem elas, skip |
| `BUBBLE_UI_RUN_AS_USER_ID` | — | id único do usuário que os testes logados usam; sem ele, skip |
| `BUBBLE_UI_APP_VERSION` | `version-test` | vazio aponta para a versão live |
| `BUBBLE_UI_HEADED` | — | `1` abre o navegador visível |

Exemplo completo:

```bash
export BUBBLE_UI_APP_ID=meu-app
export BUBBLE_UI_PROFILE=meu-perfil
export BUBBLE_UI_PAGE=index
export BUBBLE_UI_ELEMENT_IDS=btn-entrar,tx-titulo
export BUBBLE_PREVIEW_USER=... BUBBLE_PREVIEW_PW=...
python -m pytest tests/ui -q
```

## Teste logado como um usuário do app

`test_exposed_ids.py` carrega a página como visitante anônimo. Para um teste E2E de verdade
existe a fixture `page_as_user`, uma fábrica:

```python
def test_algo(page_as_user, run_as_user_id, ui_base_url):
    page = page_as_user(run_as_user_id, page="test_mcp_new")
    page.goto(f"{ui_base_url}/test_mcp_new")
    ...
```

Por baixo ela chama `bubble_run_as`, que reproduz o Run as do editor por HTTP e devolve um
`storage_state` do Playwright; a fixture carrega esse estado num contexto novo. O app trata a
página como aquele usuário — sem editor aberto, sem clique, sem seletor da UI da Bubble.

Cada chamada abre **um contexto próprio**. Isso importa para o caso mais comum de teste de
permissão: pedir dois usuários e verificar que um não enxerga o dado do outro só funciona se as
sessões estiverem de fato separadas.

### De onde vem o `user_id`

O `bubble_run_as` pede o id único do Bubble, não o email. Quem resolve isso é o projeto
`bubble-cli`, que espelha o app em SQLite pela Data API e se expõe por MCP:

```
bubble(["pull", "--types", "User"])
query("SELECT _id, email FROM User WHERE email = 'alguem@exemplo.com'")
```

O `_id` da tabela espelhada **é** o id único do Bubble (`db.py`: `"_id": "TEXT PRIMARY KEY"`).

O `bubble_run_as` também aceita `email` direto e faz a busca sozinho, sem precisar do CLI: basta
um token de Data API em `BUBBLE_DATA_API_TOKEN`, ou um `bubble.json` com `app_id` e `api_key`
apontado por `data_api_dir`. Nos dois casos o app precisa da Data API ligada
(`api-data-enabled`) e do tipo exposto (`set_data_type_api_exposure`).

Na falta de tudo isso, o id também aparece na aba Data do editor.

## Gravar um teste novo

```bash
python tests/ui/record.py --profile meu-perfil --app-id meu-app
python tests/ui/record.py --profile meu-perfil --app-id meu-app --editor   # fluxo de Run as
python tests/ui/record.py --profile meu-perfil --app-id meu-app --login    # só logar
```

Abre o navegador com o **perfil persistente do bubble-mcp** — o mesmo diretório que o
`bubble_session_login` usa — então o editor já está logado. O Inspector do Playwright abre
junto; aperte Record, use o app, e copie o código gerado para um teste aqui do lado.

O `playwright codegen` sozinho não serve para este app: ele não tem como responder ao Basic, e
o desafio vem antes de qualquer página renderizar.

### Run as

Quando você clica em Run as no editor, o Bubble abre o app em **outra aba**. O gravador segue,
porque o Inspector grava o `BrowserContext` inteiro e não uma aba só — toda aba aberta a partir
de uma página do contexto nasce dentro dele.

Duas ressalvas:

- Só um Chromium pode segurar o diretório de perfil por vez. Feche qualquer outra sessão de
  browser do MCP (um `bubble_session_login` aberto, uma leitura de node ao vivo) antes de gravar.
- **Não grave o clique no Run as.** Ele não replaya: o seletor é da UI do editor, e a linha do
  usuário na aba Data tem um id gerado por linha, que nunca se repete. Use a gravação só para
  capturar o percurso **dentro** do app, e obtenha a sessão pela fixture `page_as_user` acima —
  ela chama `bubble_run_as`, que faz o mesmo por HTTP e é reexecutável.

### Cuidado com o que você digita gravando

O gravador escreve o que você digitou como literal no código. Se a gravação passar por uma tela
de login, a senha vai parar dentro do arquivo `.py`. Tire de lá e troque por variável de
ambiente antes de commitar.

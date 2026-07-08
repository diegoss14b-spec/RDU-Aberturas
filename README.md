# Mesa de Aberturas (valor) — roda na nuvem

Este repositório atualiza sozinho o site **https://valor-rdu.netlify.app** de 6 em 6 horas,
pelo **GitHub Actions**, sem depender do computador de casa estar ligado.

O que ele faz a cada rodada: captura as odds das 4 casas (Betano, Superbet, 7k, EstrelaBet),
monta a Mesa de Aberturas (`build_board.py`) e publica no Netlify (`deploy.py`).
Não usa a Sofa nem nada pesado — os dados dos modelos vêm num pacote pequeno
(`data/pricer_data.json`, ~35 KB) que o Claude regenera no PC quando os modelos mudam.

## Passo a passo pra ligar (uma vez só)

**1. Criar o repositório no GitHub**
- Entre em https://github.com/new
- Nome: `mesa-aberturas` (ou o que quiser). Pode deixar **Privado**.
- Crie o repositório (sem README, .gitignore ou licença — já tem tudo aqui).

**2. Enviar os arquivos** (o jeito mais fácil, sem terminal)
- Instale o **GitHub Desktop** (https://desktop.github.com) e faça login.
- File → *Add Local Repository* → aponte pra esta pasta `valor-app`.
- Ele vai oferecer *"create a repository"* — aceite, então *Publish repository* escolhendo o repo que você criou no passo 1.
- (Alternativa por terminal: `git init && git add . && git commit -m "valor" && git branch -M main && git remote add origin <url-do-repo> && git push -u origin main`)

**3. Adicionar o token do Netlify** (pra ele conseguir publicar)
- No PC, o token está em `netlify_config.json` (campo `"token"`). Copie esse valor.
- No GitHub, no repositório: **Settings → Secrets and variables → Actions → New repository secret**
- Nome: `NETLIFY_TOKEN` · Valor: cole o token · **Add secret**.

**4. Pronto.** Vá em **Actions** no repositório:
- O fluxo *"Mesa de Aberturas (valor)"* roda automático de 6/6h.
- Pra testar na hora: abra o fluxo → **Run workflow** (botão à direita).

## Manutenção
- Nada no dia a dia. As tarefas locais do valor (RDUOdds*) podem ser desligadas — a nuvem assume.
- Quando os modelos recalibrarem no PC, o Claude roda `export_pricer_data.py` e você reenvia
  o `data/pricer_data.json` atualizado (um commit). É raro.

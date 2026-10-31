// Termos de uso e Política de privacidade (LGPD). Páginas públicas: /termos e /privacidade.
// Mudou o texto? Mude TERMS_VERSION em backend/app/core/config.py (e a data abaixo): todos aceitam de novo.
import { useState } from 'react'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { Link } from '../lib/router'
import { useSession } from '../lib/session'

export const LEGAL_DATE = '31 de outubro de 2026'
const CONTACT = 'suporte@finfam.net.br'

function LegalShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="legal">
      <header className="legal-top">
        <a href="/" className="legal-brand"><img src="/logo.svg" alt="" width={28} height={28} /> FinFam</a>
        <nav className="row gap">
          <Link to="/termos">Termos de uso</Link>
          <Link to="/privacidade">Privacidade</Link>
        </nav>
      </header>
      <main className="legal-main">
        <h1>{title}</h1>
        <p className="muted">Versão de {LEGAL_DATE}. Dúvidas: <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.</p>
        {children}
      </main>
    </div>
  )
}

export function TermsPage() {
  return (
    <LegalShell title="Termos de uso">
      <h2>1. O que é o FinFam</h2>
      <p>O FinFam é um aplicativo para organizar as finanças da família: gastos, cartões, contas a pagar, rendas,
        metas e investimentos, no site e no bot do Telegram. Ao criar uma conta ou usar o FinFam você concorda com
        estes termos e com a <Link to="/privacidade">Política de privacidade</Link>.</p>

      <h2>2. Conta e família</h2>
      <ul>
        <li>Para usar, você precisa ter 18 anos ou mais e informar um e-mail válido, que será confirmado.</li>
        <li>Quem cria a conta é o <b>responsável pela família</b>: convida as outras pessoas, que passam a ver os gastos,
          rendas e metas da família. Convide só quem mora com você ou divide as contas.</li>
        <li>Você é responsável por guardar a sua senha e pelo que é feito com a sua conta. Recomendamos ligar a
          verificação em duas etapas.</li>
      </ul>

      <h2>3. Bancos conectados (Open Finance)</h2>
      <ul>
        <li>A conexão com bancos é feita pela <b>Pluggy</b>, empresa autorizada a operar no Open Finance. A senha do
          banco é digitada na janela da Pluggy: o FinFam nunca a vê nem a guarda.</li>
        <li>Você escolhe quais bancos conectar e pode desconectar a qualquer momento. Os lançamentos que chegam do banco
          só viram despesas ou rendas no FinFam depois que você aprova (ou se você ligar o lançamento automático).</li>
        <li>A chave da Pluggy é de quem a cadastrou. Se ela deixar a família usar, os bancos conectados pelos outros
          ficam na conta Pluggy dela.</li>
        <li>A Pluggy tem os próprios termos e pode cobrar pelo uso, diretamente de quem tem a conta Pluggy.</li>
      </ul>

      <h2>4. Inteligência artificial</h2>
      <p>Se a família cadastrar uma chave de IA (Gemini, Claude, ChatGPT ou compatível), o bot envia ao provedor
        escolhido as mensagens, fotos e áudios que você mandar, para entender o pedido. A IA pode errar: nada é gravado
        sem a sua confirmação. O uso da chave segue os termos e a cobrança do provedor.</p>

      <h2>5. O que o FinFam não é</h2>
      <p>O FinFam é uma ferramenta de organização. Ele <b>não</b> é banco, não movimenta dinheiro, não faz pagamentos e
        não dá consultoria financeira, contábil ou jurídica. Valores, saldos e faturas mostrados dependem do que você
        lança e do que o banco informa: confira com o seu banco antes de tomar decisões.</p>

      <h2>6. Uso adequado</h2>
      <p>Não é permitido usar o FinFam para fins ilegais, tentar acessar dados de outras famílias, sobrecarregar ou
        atacar o serviço, nem cadastrar dados de terceiros sem autorização. Contas que fizerem isso podem ser suspensas.</p>

      <h2>7. Planos, teste e encerramento</h2>
      <ul>
        <li>Novas famílias têm um período de teste. Depois, o acesso depende do plano contratado.</li>
        <li>Você pode excluir a sua conta a qualquer momento em <b>Perfil e acesso → Excluir conta</b> (quem criou a
          família também pode excluir a família inteira), ou pedir pelo e-mail <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.</li>
      </ul>

      <h2>8. Disponibilidade e responsabilidade</h2>
      <p>Trabalhamos para manter o FinFam no ar e os dados seguros, com cópias de segurança diárias e criptografadas,
        mas o serviço pode ficar indisponível por manutenção ou falhas, inclusive de terceiros (bancos, Pluggy, Telegram,
        provedores de IA). Na medida permitida pela lei, o FinFam não responde por decisões tomadas com base nas
        informações do aplicativo, nem por indisponibilidade de serviços de terceiros.</p>

      <h2>9. Mudanças nestes termos</h2>
      <p>Se estes termos mudarem, avisamos no aplicativo e pedimos que você aceite a nova versão no próximo acesso.</p>

      <h2>10. Lei aplicável</h2>
      <p>Estes termos seguem as leis do Brasil, incluindo o Código de Defesa do Consumidor e a Lei Geral de Proteção de
        Dados (Lei 13.709/2018).</p>
    </LegalShell>
  )
}

export function PrivacyPage() {
  return (
    <LegalShell title="Política de privacidade">
      <p>Esta política explica quais dados o FinFam guarda, para quê, com quem compartilha e como você exerce os seus
        direitos pela Lei Geral de Proteção de Dados (LGPD). O FinFam é o controlador dos dados; o contato do
        encarregado (DPO) é <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.</p>

      <h2>1. Dados que guardamos</h2>
      <ul>
        <li><b>Cadastro:</b> nome, e-mail, foto (se você enviar) e senha — a senha é guardada só como hash, nunca em texto.</li>
        <li><b>Finanças que você lança ou importa:</b> despesas, rendas, cartões (só os 4 últimos dígitos), contas a
          pagar, metas, investimentos, regras de categoria e extratos que você importar.</li>
        <li><b>Bancos conectados (Open Finance), só com o seu consentimento:</b> contas, saldos, lançamentos, faturas,
          limites e investimentos dos bancos que <b>você</b> conectar pela Pluggy. Não temos acesso à sua senha do banco.</li>
        <li><b>Telegram:</b> o identificador da conversa com o bot, o seu nome de usuário e as mensagens que você manda
          para ele (usadas só para responder).</li>
        <li><b>Inteligência artificial:</b> a chave da família fica criptografada; o texto, a foto ou o áudio que você
          manda ao bot vão ao provedor escolhido pela família, só para interpretar o pedido.</li>
        <li><b>Registros técnicos:</b> endereço IP e horário de acesso, para segurança (limite de tentativas de login,
          prevenção de abuso) e para corrigir erros.</li>
      </ul>

      <h2>2. Para que usamos</h2>
      <p>Para prestar o serviço (mostrar e calcular as suas finanças, conferir faturas, mandar lembretes e avisos que
        você ligar), para a segurança da sua conta e para cumprir obrigações legais. <b>Não vendemos seus dados, não os
        usamos para publicidade e não os compartilhamos para marketing.</b></p>

      <h2>3. Bases legais</h2>
      <p>Execução do contrato (o uso do aplicativo), consentimento (bancos conectados, inteligência artificial e
        Telegram, que você liga e desliga quando quiser), legítimo interesse (segurança e prevenção de fraude) e
        cumprimento de obrigação legal.</p>

      <h2>4. Com quem compartilhamos</h2>
      <ul>
        <li><b>Sua família no FinFam:</b> as pessoas da família veem os gastos, rendas, cartões e metas da família. Os
          bancos conectados aparecem só para quem conectou, a não ser que essa pessoa os compartilhe com a família.</li>
        <li><b>Operadores que nos ajudam a prestar o serviço:</b> Pluggy (conexão com bancos), Telegram (mensagens do
          bot), o provedor de IA escolhido pela família (como Google, Anthropic ou OpenAI), o provedor de e-mail (envio de
          confirmações e redefinição de senha) e a Cloudflare (rede e proteção do site). Cada um recebe só o necessário
          para a sua parte. Alguns deles podem tratar dados fora do Brasil, com as garantias previstas na LGPD.</li>
        <li><b>Autoridades:</b> só quando a lei ou uma ordem judicial exigir.</li>
      </ul>

      <h2>5. Segurança</h2>
      <p>Senhas com hash, chaves de bancos e de IA criptografadas, acesso só por conexão segura (HTTPS), verificação em
        duas etapas opcional, isolamento entre famílias, limite de tentativas de login e cópias de segurança diárias
        criptografadas guardadas fora do servidor principal. Nenhum sistema é 100% seguro: se houver um incidente que
        afete seus dados, avisaremos você e a ANPD conforme a lei.</p>

      <h2>6. Por quanto tempo guardamos</h2>
      <ul>
        <li>Enquanto a sua conta existir.</li>
        <li>Ao desconectar um banco, os dados que vieram dele são apagados (as despesas e rendas que você aprovou ficam).</li>
        <li>Ao excluir a conta (em Perfil e acesso → Excluir conta), os dados são apagados do FinFam na hora e os
          bancos conectados são desconectados na Pluggy.</li>
        <li>As cópias de segurança do servidor inteiro são criptografadas, só a administração do FinFam consegue abri-las
          e são usadas apenas para recuperar o serviço em caso de falha. Elas são descartadas aos poucos (as mais
          antigas primeiro) e não são usadas para restaurar uma conta que você excluiu.</li>
        <li>Podemos manter o mínimo exigido por lei pelo prazo legal.</li>
      </ul>

      <h2>7. Seus direitos</h2>
      <p>Você pode pedir a qualquer momento: confirmação de que tratamos seus dados, acesso, correção, portabilidade,
        anonimização ou exclusão, informação sobre com quem compartilhamos e revogação do consentimento. Escreva para
        {' '}<a href={`mailto:${CONTACT}`}>{CONTACT}</a> com o e-mail da conta; respondemos em até 15 dias. Pelo próprio
        site você já pode corrigir seus dados, exportar as despesas (Excel/PDF), desconectar bancos e o Telegram,
        remover a chave de IA e excluir a conta. Você também pode reclamar à Autoridade Nacional de Proteção de Dados (ANPD).</p>

      <h2>8. Armazenamento no navegador</h2>
      <p>O site guarda o seu login no próprio navegador (armazenamento local) para você não precisar entrar toda hora.
        Não usamos cookies de rastreamento nem de publicidade. As fontes do site vêm do Google Fonts, que recebe o
        endereço IP de quem abre a página.</p>

      <h2>9. Crianças e adolescentes</h2>
      <p>O FinFam é para maiores de 18 anos. Não cadastre menores como pessoas da família.</p>

      <h2>10. Mudanças nesta política</h2>
      <p>Se esta política mudar, avisamos no aplicativo e pedimos que você aceite a nova versão no próximo acesso.</p>
    </LegalShell>
  )
}

export function TermsLinks() {
  return <><Link to="/termos">Termos de uso</Link> e <Link to="/privacidade">Política de privacidade</Link></>
}

/** Faixa no topo do app enquanto a pessoa não aceitar a versão atual (convidados, ou termos novos). */
export function TermsBanner() {
  const { me, refreshMe, toast } = useSession()
  const [busy, setBusy] = useState(false)
  if (!me || me.via_api_token || !me.terms || me.terms.accepted) return null
  const accept = async () => {
    setBusy(true)
    try { await api.post('/auth/accept-terms'); await refreshMe() } catch (e) { toast((e as Error).message, 'danger') }
    setBusy(false)
  }
  return (
    <div className="notice" role="status">
      <span>📄 {me.terms.accepted_at ? 'Atualizamos os' : 'Leia os'} <TermsLinks /> do FinFam.</span>
      <Button busy={busy} onClick={accept}>Li e aceito</Button>
    </div>
  )
}

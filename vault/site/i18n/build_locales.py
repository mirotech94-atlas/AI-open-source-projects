#!/usr/bin/env python3
"""Generate the eight translated copies of site/index.html.

WHY THIS APPROACH (read before touching anything below):

The site is static files behind nginx with no build step and no framework
(see CLAUDE.md / the site's own header comment). Two ways to add languages
without a build step were on the table:

  (a) one full HTML file per locale, generated ahead of time by a script
      you commit and re-run by hand, or
  (b) one HTML file plus a JSON string table applied client-side at load.

(b) was rejected on its own terms, not on style. A Hindi reader's first
paint would be English until the script fetches the table and rewrites the
DOM -- a visible flash of the wrong language every single load, on every
connection speed. And with JavaScript off the page would stay English
forever, which fails the site's own stated principle (see site/app.js's
header comment) that the page has to keep working without a script. Neither
failure is acceptable for a marketing site whose whole pitch is reliability.

So: (a). This script is run BY HAND, by whoever edits the English copy in
`site/index.html`, and its output -- `site/ar/index.html`, `site/es/index.html`,
etc. -- is committed like any other file. Nginx never sees this script and
never runs Python; it just serves nine plain HTML files. There is still no
build step in the deployed sense: `deploy/setup-site.sh` rsyncs the `site/`
directory exactly as it finds it in git.

HOW IT WORKS:

`site/index.html` (English) is the one hand-edited source. Every other
locale is derived from it by literal and regex substitution, keyed by the
tables below, so structure (markup, ids, hrefs, the data-plan/data-interval
attributes the pricing tests parse) can never drift between locales -- only
the translated text moves. Re-run this script after every edit to the
English copy:

    python3 site/i18n/build_locales.py

CAVEAT, in the loudest comment in this file: every non-English string below
was produced by me (an LLM), not by a native speaker. It is a reasonable
first draft, not a proofread translation. Before this site is promoted in
any of these languages, a native speaker needs to read that language's page
end to end. This is stated plainly in the delivery report too -- it is not
hidden here for someone to discover later.
"""

import pathlib
import re
import sys

SITE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = SITE / "index.html"

LOCALES = ["ar", "es", "fr", "de", "pt", "it", "zh", "hi"]
RTL_LOCALES = {"ar"}

# The hreflang code used in <link rel="alternate">, which prefers the
# language+script form for Chinese; the footer's own <a hreflang> stays the
# short "zh" (see site/index.html's comment) since that attribute is
# informational, not the one search engines index.
HREFLANG_CODE = {
    "ar": "ar", "es": "es", "fr": "fr", "de": "de",
    "pt": "pt", "it": "it", "zh": "zh-Hans", "hi": "hi",
}

# Native-script label shown in the footer switcher for each locale -- these
# are NOT translated per-page; every locale's switcher shows every language
# in its own script, which is the standard convention (a Hindi reader still
# looks for "Deutsch", not a Hindi transliteration of it).
NATIVE_LABEL = {
    "en": "English", "ar": "العربية", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "it": "Italiano", "zh": "中文",
    "hi": "हिन्दी",
}

# ---------------------------------------------------------------------------
# LITERAL: single-line fragments, safe to match with a plain str.replace()
# because they carry no internal newlines the way a wrapped <p> does. Each
# value is copied byte-for-byte out of site/index.html (the English side),
# so a mismatch here is a bug in this file, not a moved line in the HTML.
# Replacement is unbounded (str.replace with no count) on purpose: a few of
# these fragments are byte-identical in two places (e.g. the "Sign in" link
# in the header and again in the footer's Account group), and the correct
# translation is the same text both times.
# ---------------------------------------------------------------------------

LITERAL = {
    "title_tag": {
        "en": '<title>Vault — watch your network, and fix it</title>',
        "ar": '<title>Vault — راقب شبكتك، وأصلحها</title>',
        "es": '<title>Vault: vigila tu red y repárala</title>',
        "fr": '<title>Vault : surveillez votre réseau, et réparez-le</title>',
        "de": '<title>Vault – behalten Sie Ihr Netzwerk im Blick, und reparieren Sie es</title>',
        "pt": '<title>Vault: vigie a sua rede, e conserte-a</title>',
        "it": '<title>Vault: sorveglia la tua rete, e riparala</title>',
        "zh": '<title>Vault——守护你的网络，并修复它</title>',
        "hi": '<title>Vault — अपने नेटवर्क पर नज़र रखें, और उसे ठीक करें</title>',
    },
    "meta_desc": {
        "en": '<meta name="description" content="Vault watches the devices, servers, websites and DNS traffic on a network, tells you when something changes, and fixes the routine problems itself.">',
        "ar": '<meta name="description" content="يراقب Vault الأجهزة والخوادم والمواقع وحركة DNS على الشبكة، ويخبرك عندما يتغيّر شيء، ويصلح المشاكل الروتينية بنفسه.">',
        "es": '<meta name="description" content="Vault vigila los dispositivos, servidores, sitios web y el tráfico DNS de una red, te avisa cuando algo cambia, y resuelve por su cuenta los problemas rutinarios.">',
        "fr": '<meta name="description" content="Vault surveille les appareils, serveurs, sites web et le trafic DNS d’un réseau, vous prévient quand quelque chose change, et corrige lui-même les problèmes courants.">',
        "de": '<meta name="description" content="Vault überwacht die Geräte, Server, Websites und den DNS-Verkehr in einem Netzwerk, benachrichtigt Sie, wenn sich etwas ändert, und behebt Routineprobleme von selbst.">',
        "pt": '<meta name="description" content="O Vault vigia os dispositivos, servidores, sites e o tráfego DNS de uma rede, avisa-o quando algo muda, e resolve por conta própria os problemas rotineiros.">',
        "it": '<meta name="description" content="Vault sorveglia i dispositivi, i server, i siti web e il traffico DNS di una rete, ti avvisa quando qualcosa cambia, e risolve da solo i problemi di routine.">',
        "zh": '<meta name="description" content="Vault 监控网络上的设备、服务器、网站和 DNS 流量，一旦有变化就通知你，并自行修复常见的日常问题。">',
        "hi": '<meta name="description" content="Vault किसी नेटवर्क पर डिवाइस, सर्वर, वेबसाइट और DNS ट्रैफ़िक पर नज़र रखता है, कुछ बदलने पर आपको बताता है, और सामान्य समस्याओं को खुद ठीक कर देता है।">',
    },
    "og_title": {
        "en": '<meta property="og:title" content="Vault — watch your network, and fix it">',
        "ar": '<meta property="og:title" content="Vault — راقب شبكتك، وأصلحها">',
        "es": '<meta property="og:title" content="Vault: vigila tu red y repárala">',
        "fr": '<meta property="og:title" content="Vault : surveillez votre réseau, et réparez-le">',
        "de": '<meta property="og:title" content="Vault – behalten Sie Ihr Netzwerk im Blick, und reparieren Sie es">',
        "pt": '<meta property="og:title" content="Vault: vigie a sua rede, e conserte-a">',
        "it": '<meta property="og:title" content="Vault: sorveglia la tua rete, e riparala">',
        "zh": '<meta property="og:title" content="Vault——守护你的网络，并修复它">',
        "hi": '<meta property="og:title" content="Vault — अपने नेटवर्क पर नज़र रखें, और उसे ठीक करें">',
    },
    "og_desc": {
        "en": '<meta property="og:description" content="Vault watches the devices, servers, websites and DNS traffic on a network, tells you when something changes, and fixes the routine problems itself.">',
        "ar": '<meta property="og:description" content="يراقب Vault الأجهزة والخوادم والمواقع وحركة DNS على الشبكة، ويخبرك عندما يتغيّر شيء، ويصلح المشاكل الروتينية بنفسه.">',
        "es": '<meta property="og:description" content="Vault vigila los dispositivos, servidores, sitios web y el tráfico DNS de una red, te avisa cuando algo cambia, y resuelve por su cuenta los problemas rutinarios.">',
        "fr": '<meta property="og:description" content="Vault surveille les appareils, serveurs, sites web et le trafic DNS d’un réseau, vous prévient quand quelque chose change, et corrige lui-même les problèmes courants.">',
        "de": '<meta property="og:description" content="Vault überwacht die Geräte, Server, Websites und den DNS-Verkehr in einem Netzwerk, benachrichtigt Sie, wenn sich etwas ändert, und behebt Routineprobleme von selbst.">',
        "pt": '<meta property="og:description" content="O Vault vigia os dispositivos, servidores, sites e o tráfego DNS de uma rede, avisa-o quando algo muda, e resolve por conta própria os problemas rotineiros.">',
        "it": '<meta property="og:description" content="Vault sorveglia i dispositivi, i server, i siti web e il traffico DNS di una rete, ti avvisa quando qualcosa cambia, e risolve da solo i problemi di routine.">',
        "zh": '<meta property="og:description" content="Vault 监控网络上的设备、服务器、网站和 DNS 流量，一旦有变化就通知你，并自行修复常见的日常问题。">',
        "hi": '<meta property="og:description" content="Vault किसी नेटवर्क पर डिवाइस, सर्वर, वेबसाइट और DNS ट्रैफ़िक पर नज़र रखता है, कुछ बदलने पर आपको बताता है, और सामान्य समस्याओं को खुद ठीक कर देता है।">',
    },
    "skip": {
        "en": '<a class="skip" href="#main">Skip to content</a>',
        "ar": '<a class="skip" href="#main">تخطَّ إلى المحتوى</a>',
        "es": '<a class="skip" href="#main">Saltar al contenido</a>',
        "fr": '<a class="skip" href="#main">Passer au contenu</a>',
        "de": '<a class="skip" href="#main">Zum Inhalt springen</a>',
        "pt": '<a class="skip" href="#main">Ir para o conteúdo</a>',
        "it": '<a class="skip" href="#main">Vai al contenuto</a>',
        "zh": '<a class="skip" href="#main">跳到正文内容</a>',
        "hi": '<a class="skip" href="#main">सामग्री पर जाएँ</a>',
    },
}

LITERAL.update({
    "nav_live": {
        "en": '<a class="jump" href="#live">Live</a>',
        "ar": '<a class="jump" href="#live">مباشر</a>',
        "es": '<a class="jump" href="#live">En vivo</a>',
        "fr": '<a class="jump" href="#live">En direct</a>',
        "de": '<a class="jump" href="#live">Live</a>',
        "pt": '<a class="jump" href="#live">Ao vivo</a>',
        "it": '<a class="jump" href="#live">Dal vivo</a>',
        "zh": '<a class="jump" href="#live">实时</a>',
        "hi": '<a class="jump" href="#live">लाइव</a>',
    },
    "nav_watches": {
        "en": '<a class="jump" href="#watches">What it watches</a>',
        "ar": '<a class="jump" href="#watches">ما الذي يراقبه</a>',
        "es": '<a class="jump" href="#watches">Qué vigila</a>',
        "fr": '<a class="jump" href="#watches">Ce qu\'il surveille</a>',
        "de": '<a class="jump" href="#watches">Was es überwacht</a>',
        "pt": '<a class="jump" href="#watches">O que ele vigia</a>',
        "it": '<a class="jump" href="#watches">Cosa sorveglia</a>',
        "zh": '<a class="jump" href="#watches">它监控什么</a>',
        "hi": '<a class="jump" href="#watches">यह क्या देखता है</a>',
    },
    "nav_how": {
        "en": '<a class="jump" href="#how">How it works</a>',
        "ar": '<a class="jump" href="#how">كيف يعمل</a>',
        "es": '<a class="jump" href="#how">Cómo funciona</a>',
        "fr": '<a class="jump" href="#how">Comment ça marche</a>',
        "de": '<a class="jump" href="#how">So funktioniert es</a>',
        "pt": '<a class="jump" href="#how">Como funciona</a>',
        "it": '<a class="jump" href="#how">Come funziona</a>',
        "zh": '<a class="jump" href="#how">工作原理</a>',
        "hi": '<a class="jump" href="#how">यह कैसे काम करता है</a>',
    },
    "nav_pricing": {
        'en': '<a class="jump" href="#pricing">Free trial</a>',
        'ar': '<a class="jump" href="#pricing">تجربة مجانية</a>',
        'es': '<a class="jump" href="#pricing">Prueba gratis</a>',
        'fr': '<a class="jump" href="#pricing">Essai gratuit</a>',
        'de': '<a class="jump" href="#pricing">Kostenlos testen</a>',
        'pt': '<a class="jump" href="#pricing">Teste grátis</a>',
        'it': '<a class="jump" href="#pricing">Prova gratuita</a>',
        'zh': '<a class="jump" href="#pricing">免费试用</a>',
        'hi': '<a class="jump" href="#pricing">मुफ़्त ट्रायल</a>',
    },
    "nav_faq": {
        "en": '<a class="jump" href="#faq">FAQ</a>',
        "ar": '<a class="jump" href="#faq">الأسئلة الشائعة</a>',
        "es": '<a class="jump" href="#faq">Preguntas frecuentes</a>',
        "fr": '<a class="jump" href="#faq">FAQ</a>',
        "de": '<a class="jump" href="#faq">FAQ</a>',
        "pt": '<a class="jump" href="#faq">Perguntas frequentes</a>',
        "it": '<a class="jump" href="#faq">FAQ</a>',
        "zh": '<a class="jump" href="#faq">常见问题</a>',
        "hi": '<a class="jump" href="#faq">सामान्य प्रश्न</a>',
    },
    "signin": {
        "en": '<a href="https://my.guarder.io/login">Log in</a>',
        "ar": '<a href="https://my.guarder.io/login">تسجيل الدخول</a>',
        "es": '<a href="https://my.guarder.io/login">Iniciar sesión</a>',
        "fr": '<a href="https://my.guarder.io/login">Se connecter</a>',
        "de": '<a href="https://my.guarder.io/login">Anmelden</a>',
        "pt": '<a href="https://my.guarder.io/login">Entrar</a>',
        "it": '<a href="https://my.guarder.io/login">Accedi</a>',
        "zh": '<a href="https://my.guarder.io/login">登录</a>',
        "hi": '<a href="https://my.guarder.io/login">साइन इन करें</a>',
    },
})

LITERAL.update({
    "hero_pill": {
        'en': '<p class="pill">Network watchdog &mdash; three months free, no card</p>',
        'ar': '<p class="pill">حارس الشبكة &mdash; ثلاثة أشهر مجانًا، بلا بطاقة</p>',
        'es': '<p class="pill">Vigilante de red &mdash; tres meses gratis, sin tarjeta</p>',
        'fr': '<p class="pill">Veilleur de réseau &mdash; trois mois gratuits, sans carte</p>',
        'de': '<p class="pill">Netzwerk-Wächter &mdash; drei Monate kostenlos, ohne Karte</p>',
        'pt': '<p class="pill">Vigilante de rede &mdash; três meses grátis, sem cartão</p>',
        'it': '<p class="pill">Vaulto di rete &mdash; tre mesi gratis, senza carta</p>',
        'zh': '<p class="pill">网络守护 &mdash; 三个月免费，无需银行卡</p>',
        'hi': '<p class="pill">नेटवर्क वॉचडॉग &mdash; तीन महीने मुफ़्त, बिना कार्ड</p>',
    },
    "hero_h1": {
        "en": '<h1 id="hero-h">Something changed on your network.<br><span class="hl">Vault noticed before you did.</span></h1>',
        "ar": '<h1 id="hero-h">تغيّر شيء في شبكتك.<br><span class="hl">لاحظه Vault قبل أن تلاحظه أنت.</span></h1>',
        "es": '<h1 id="hero-h">Algo cambió en tu red.<br><span class="hl">Vault lo notó antes que tú.</span></h1>',
        "fr": '<h1 id="hero-h">Quelque chose a changé sur votre réseau.<br><span class="hl">Vault l\'a remarqué avant vous.</span></h1>',
        "de": '<h1 id="hero-h">Auf Ihrem Netzwerk hat sich etwas verändert.<br><span class="hl">Vault hat es vor Ihnen bemerkt.</span></h1>',
        "pt": '<h1 id="hero-h">Algo mudou na sua rede.<br><span class="hl">O Vault percebeu antes de você.</span></h1>',
        "it": '<h1 id="hero-h">Qualcosa è cambiato sulla tua rete.<br><span class="hl">Vault l\'ha notato prima di te.</span></h1>',
        "zh": '<h1 id="hero-h">你的网络发生了变化。<br><span class="hl">Vault 比你先发现了它。</span></h1>',
        "hi": '<h1 id="hero-h">आपके नेटवर्क पर कुछ बदल गया।<br><span class="hl">Vault ने इसे आपसे पहले नोटिस कर लिया।</span></h1>',
    },
    "hero_cta_start": {
        "en": '<a class="btn" href="/signup.html">Start three months free</a>',
        "ar": '<a class="btn" href="/signup.html">ابدأ ثلاثة أشهر مجانية</a>',
        "es": '<a class="btn" href="/signup.html">Empieza tres meses gratis</a>',
        "fr": '<a class="btn" href="/signup.html">Commencez trois mois gratuits</a>',
        "de": '<a class="btn" href="/signup.html">Drei kostenlose Monate starten</a>',
        "pt": '<a class="btn" href="/signup.html">Comece três meses grátis</a>',
        "it": '<a class="btn" href="/signup.html">Inizia tre mesi gratuiti</a>',
        "zh": '<a class="btn" href="/signup.html">开始三个月免费试用</a>',
        "hi": '<a class="btn" href="/signup.html">तीन महीने मुफ़्त शुरू करें</a>',
    },
    "hero_cta_see": {
        "en": '<a class="btn ghost" href="#live">See it working</a>',
        "ar": '<a class="btn ghost" href="#live">شاهده أثناء العمل</a>',
        "es": '<a class="btn ghost" href="#live">Míralo en acción</a>',
        "fr": '<a class="btn ghost" href="#live">Voyez-le en action</a>',
        "de": '<a class="btn ghost" href="#live">In Aktion sehen</a>',
        "pt": '<a class="btn ghost" href="#live">Veja-o em ação</a>',
        "it": '<a class="btn ghost" href="#live">Guardalo in azione</a>',
        "zh": '<a class="btn ghost" href="#live">看看它的实际效果</a>',
        "hi": '<a class="btn ghost" href="#live">इसे काम करते देखें</a>',
    },
})

LITERAL.update({
    "poll_stamp": {
        "en": '<p class="poll" id="feed-stamp">last scan &mdash;</p>',
        "ar": '<p class="poll" id="feed-stamp">آخر فحص &mdash;</p>',
        "es": '<p class="poll" id="feed-stamp">último análisis &mdash;</p>',
        "fr": '<p class="poll" id="feed-stamp">dernière analyse &mdash;</p>',
        "de": '<p class="poll" id="feed-stamp">letzter Scan &mdash;</p>',
        "pt": '<p class="poll" id="feed-stamp">última verificação &mdash;</p>',
        "it": '<p class="poll" id="feed-stamp">ultima scansione &mdash;</p>',
        "zh": '<p class="poll" id="feed-stamp">上次扫描 &mdash;</p>',
        "hi": '<p class="poll" id="feed-stamp">आख़िरी स्कैन &mdash;</p>',
    },
    "problem_eyebrow": {
        "en": '<p class="eyebrow">The problem</p>',
        "ar": '<p class="eyebrow">المشكلة</p>',
        "es": '<p class="eyebrow">El problema</p>',
        "fr": '<p class="eyebrow">Le problème</p>',
        "de": '<p class="eyebrow">Das Problem</p>',
        "pt": '<p class="eyebrow">O problema</p>',
        "it": '<p class="eyebrow">Il problema</p>',
        "zh": '<p class="eyebrow">问题所在</p>',
        "hi": '<p class="eyebrow">समस्या</p>',
    },
    "problem_h2": {
        "en": '<h2 id="problem-h">A network never tells you it broke.<br><span class="hl">A person does, hours later.</span></h2>',
        "ar": '<h2 id="problem-h">الشبكة لا تخبرك أبدًا أنها تعطّلت.<br><span class="hl">شخص ما يفعل ذلك، بعد ساعات.</span></h2>',
        "es": '<h2 id="problem-h">Una red nunca te dice que se rompió.<br><span class="hl">Una persona sí, horas después.</span></h2>',
        "fr": '<h2 id="problem-h">Un réseau ne vous dit jamais qu\'il est en panne.<br><span class="hl">Une personne le fait, des heures plus tard.</span></h2>',
        "de": '<h2 id="problem-h">Ein Netzwerk sagt Ihnen nie, dass es kaputt ist.<br><span class="hl">Ein Mensch tut das, Stunden später.</span></h2>',
        "pt": '<h2 id="problem-h">Uma rede nunca lhe diz que quebrou.<br><span class="hl">Uma pessoa diz, horas depois.</span></h2>',
        "it": '<h2 id="problem-h">Una rete non ti dice mai che si è rotta.<br><span class="hl">Una persona sì, ore dopo.</span></h2>',
        "zh": '<h2 id="problem-h">网络从不会告诉你它出故障了。<br><span class="hl">几个小时后，会有人告诉你。</span></h2>',
        "hi": '<h2 id="problem-h">नेटवर्क कभी नहीं बताता कि वह खराब हो गया।<br><span class="hl">एक इंसान बताता है, घंटों बाद।</span></h2>',
    },
    "quote1": {
        "en": '<li>&ldquo;The Wi-Fi has been weird since Tuesday.&rdquo;</li>',
        "ar": '<li>&ldquo;الواي فاي يعمل بغرابة منذ يوم الثلاثاء.&rdquo;</li>',
        "es": '<li>&ldquo;El wifi lleva raro desde el martes.&rdquo;</li>',
        "fr": '<li>&ldquo;Le Wi-Fi est bizarre depuis mardi.&rdquo;</li>',
        "de": '<li>&ldquo;Das WLAN spinnt schon seit Dienstag.&rdquo;</li>',
        "pt": '<li>&ldquo;O Wi-Fi está estranho desde terça-feira.&rdquo;</li>',
        "it": '<li>&ldquo;Il Wi-Fi è strano da martedì.&rdquo;</li>',
        "zh": '<li>&ldquo;Wi-Fi 从星期二开始就怪怪的。&rdquo;</li>',
        "hi": '<li>&ldquo;मंगलवार से Wi-Fi अजीब चल रहा है।&rdquo;</li>',
    },
    "quote2": {
        "en": '<li>&ldquo;Someone plugged something in. No idea what.&rdquo;</li>',
        "ar": '<li>&ldquo;وصّل أحدهم شيئًا ما. لا أعرف ماذا.&rdquo;</li>',
        "es": '<li>&ldquo;Alguien conectó algo. No sé qué.&rdquo;</li>',
        "fr": '<li>&ldquo;Quelqu\'un a branché quelque chose. Aucune idée de quoi.&rdquo;</li>',
        "de": '<li>&ldquo;Jemand hat etwas angeschlossen. Keine Ahnung, was.&rdquo;</li>',
        "pt": '<li>&ldquo;Alguém ligou uma coisa. Não sei o quê.&rdquo;</li>',
        "it": '<li>&ldquo;Qualcuno ha collegato qualcosa. Non si sa cosa.&rdquo;</li>',
        "zh": '<li>&ldquo;有人插了个什么东西。不知道是什么。&rdquo;</li>',
        "hi": '<li>&ldquo;किसी ने कुछ प्लग किया है। पता नहीं क्या।&rdquo;</li>',
    },
    "quote3": {
        "en": '<li>&ldquo;The certificate expired. On a Sunday, obviously.&rdquo;</li>',
        "ar": '<li>&ldquo;انتهت صلاحية الشهادة. يوم الأحد بالطبع.&rdquo;</li>',
        "es": '<li>&ldquo;El certificado caducó. Un domingo, cómo no.&rdquo;</li>',
        "fr": '<li>&ldquo;Le certificat a expiré. Un dimanche, évidemment.&rdquo;</li>',
        "de": '<li>&ldquo;Das Zertifikat ist abgelaufen. An einem Sonntag, natürlich.&rdquo;</li>',
        "pt": '<li>&ldquo;O certificado expirou. Num domingo, claro.&rdquo;</li>',
        "it": '<li>&ldquo;Il certificato è scaduto. Di domenica, ovviamente.&rdquo;</li>',
        "zh": '<li>&ldquo;证书过期了。当然是在星期天。&rdquo;</li>',
        "hi": '<li>&ldquo;सर्टिफ़िकेट की वैधता खत्म हो गई। रविवार को, जाहिर तौर पर।&rdquo;</li>',
    },
    "quote4": {
        "en": '<li>&ldquo;The site was down four hours before anyone noticed.&rdquo;</li>',
        "ar": '<li>&ldquo;كان الموقع معطلًا لأربع ساعات قبل أن يلاحظ أحد.&rdquo;</li>',
        "es": '<li>&ldquo;El sitio estuvo caído cuatro horas antes de que alguien se diera cuenta.&rdquo;</li>',
        "fr": '<li>&ldquo;Le site était en panne depuis quatre heures avant que quelqu\'un s\'en rende compte.&rdquo;</li>',
        "de": '<li>&ldquo;Die Website war vier Stunden down, bevor es jemand merkte.&rdquo;</li>',
        "pt": '<li>&ldquo;O site esteve fora do ar quatro horas antes de alguém notar.&rdquo;</li>',
        "it": '<li>&ldquo;Il sito era offline da quattro ore prima che qualcuno se ne accorgesse.&rdquo;</li>',
        "zh": '<li>&ldquo;网站宕机四个小时后才有人发现。&rdquo;</li>',
        "hi": '<li>&ldquo;साइट चार घंटे से डाउन थी, तब किसी को पता चला।&rdquo;</li>',
    },
})

LITERAL.update({
    "watches_h2": {
        "en": '<h2 id="watches-h">What it watches</h2>',
        "ar": '<h2 id="watches-h">ما الذي يراقبه</h2>',
        "es": '<h2 id="watches-h">Qué vigila</h2>',
        "fr": '<h2 id="watches-h">Ce qu\'il surveille</h2>',
        "de": '<h2 id="watches-h">Was es überwacht</h2>',
        "pt": '<h2 id="watches-h">O que ele vigia</h2>',
        "it": '<h2 id="watches-h">Cosa sorveglia</h2>',
        "zh": '<h2 id="watches-h">它监控什么</h2>',
        "hi": '<h2 id="watches-h">यह क्या देखता है</h2>',
    },
    "card1_h3": {
        "en": '<h3><span class="dot" aria-hidden="true"></span>Who is on the network</h3>',
        "ar": '<h3><span class="dot" aria-hidden="true"></span>من على الشبكة</h3>',
        "es": '<h3><span class="dot" aria-hidden="true"></span>Quién está en la red</h3>',
        "fr": '<h3><span class="dot" aria-hidden="true"></span>Qui est sur le réseau</h3>',
        "de": '<h3><span class="dot" aria-hidden="true"></span>Wer im Netzwerk ist</h3>',
        "pt": '<h3><span class="dot" aria-hidden="true"></span>Quem está na rede</h3>',
        "it": '<h3><span class="dot" aria-hidden="true"></span>Chi è sulla rete</h3>',
        "zh": '<h3><span class="dot" aria-hidden="true"></span>网络上有谁</h3>',
        "hi": '<h3><span class="dot" aria-hidden="true"></span>नेटवर्क पर कौन है</h3>',
    },
    "card2_h3": {
        "en": '<h3><span class="dot ok" aria-hidden="true"></span>Your servers</h3>',
        "ar": '<h3><span class="dot ok" aria-hidden="true"></span>خوادمك</h3>',
        "es": '<h3><span class="dot ok" aria-hidden="true"></span>Tus servidores</h3>',
        "fr": '<h3><span class="dot ok" aria-hidden="true"></span>Vos serveurs</h3>',
        "de": '<h3><span class="dot ok" aria-hidden="true"></span>Ihre Server</h3>',
        "pt": '<h3><span class="dot ok" aria-hidden="true"></span>Os seus servidores</h3>',
        "it": '<h3><span class="dot ok" aria-hidden="true"></span>I tuoi server</h3>',
        "zh": '<h3><span class="dot ok" aria-hidden="true"></span>你的服务器</h3>',
        "hi": '<h3><span class="dot ok" aria-hidden="true"></span>आपके सर्वर</h3>',
    },
    "card3_h3": {
        "en": '<h3><span class="dot warn" aria-hidden="true"></span>Your websites and certificates</h3>',
        "ar": '<h3><span class="dot warn" aria-hidden="true"></span>مواقعك وشهاداتك</h3>',
        "es": '<h3><span class="dot warn" aria-hidden="true"></span>Tus sitios web y certificados</h3>',
        "fr": '<h3><span class="dot warn" aria-hidden="true"></span>Vos sites web et certificats</h3>',
        "de": '<h3><span class="dot warn" aria-hidden="true"></span>Ihre Websites und Zertifikate</h3>',
        "pt": '<h3><span class="dot warn" aria-hidden="true"></span>Os seus sites e certificados</h3>',
        "it": '<h3><span class="dot warn" aria-hidden="true"></span>I tuoi siti web e certificati</h3>',
        "zh": '<h3><span class="dot warn" aria-hidden="true"></span>你的网站和证书</h3>',
        "hi": '<h3><span class="dot warn" aria-hidden="true"></span>आपकी वेबसाइट और सर्टिफ़िकेट</h3>',
    },
    "card4_h3": {
        "en": '<h3><span class="dot" aria-hidden="true"></span>Where the traffic goes</h3>',
        "ar": '<h3><span class="dot" aria-hidden="true"></span>إلى أين تذهب حركة البيانات</h3>',
        "es": '<h3><span class="dot" aria-hidden="true"></span>A dónde va el tráfico</h3>',
        "fr": '<h3><span class="dot" aria-hidden="true"></span>Où va le trafic</h3>',
        "de": '<h3><span class="dot" aria-hidden="true"></span>Wohin der Datenverkehr geht</h3>',
        "pt": '<h3><span class="dot" aria-hidden="true"></span>Para onde vai o tráfego</h3>',
        "it": '<h3><span class="dot" aria-hidden="true"></span>Dove va il traffico</h3>',
        "zh": '<h3><span class="dot" aria-hidden="true"></span>流量去向</h3>',
        "hi": '<h3><span class="dot" aria-hidden="true"></span>ट्रैफ़िक कहाँ जाता है</h3>',
    },
    "card5_h3": {
        "en": '<h3><span class="dot crit" aria-hidden="true"></span>What is exposed, and who is knocking</h3>',
        "ar": '<h3><span class="dot crit" aria-hidden="true"></span>ما المكشوف، ومن يطرق الباب</h3>',
        "es": '<h3><span class="dot crit" aria-hidden="true"></span>Qué está expuesto, y quién llama a la puerta</h3>',
        "fr": '<h3><span class="dot crit" aria-hidden="true"></span>Ce qui est exposé, et qui frappe à la porte</h3>',
        "de": '<h3><span class="dot crit" aria-hidden="true"></span>Was offen liegt, und wer anklopft</h3>',
        "pt": '<h3><span class="dot crit" aria-hidden="true"></span>O que está exposto, e quem está a bater à porta</h3>',
        "it": '<h3><span class="dot crit" aria-hidden="true"></span>Cosa è esposto, e chi bussa alla porta</h3>',
        "zh": '<h3><span class="dot crit" aria-hidden="true"></span>哪些端口暴露在外，又是谁在敲门</h3>',
        "hi": '<h3><span class="dot crit" aria-hidden="true"></span>क्या खुला हुआ है, और कौन दस्तक दे रहा है</h3>',
    },
    "card6_h3": {
        "en": '<h3><span class="dot ok" aria-hidden="true"></span>Who is around</h3>',
        "ar": '<h3><span class="dot ok" aria-hidden="true"></span>من موجود</h3>',
        "es": '<h3><span class="dot ok" aria-hidden="true"></span>Quién está presente</h3>',
        "fr": '<h3><span class="dot ok" aria-hidden="true"></span>Qui est présent</h3>',
        "de": '<h3><span class="dot ok" aria-hidden="true"></span>Wer da ist</h3>',
        "pt": '<h3><span class="dot ok" aria-hidden="true"></span>Quem está por perto</h3>',
        "it": '<h3><span class="dot ok" aria-hidden="true"></span>Chi c\'è in giro</h3>',
        "zh": '<h3><span class="dot ok" aria-hidden="true"></span>谁在附近</h3>',
        "hi": '<h3><span class="dot ok" aria-hidden="true"></span>कौन आसपास है</h3>',
    },
})

LITERAL.update({
    "how_h2": {
        "en": '<h2 id="how-h">How it works</h2>',
        "ar": '<h2 id="how-h">كيف يعمل</h2>',
        "es": '<h2 id="how-h">Cómo funciona</h2>',
        "fr": '<h2 id="how-h">Comment ça marche</h2>',
        "de": '<h2 id="how-h">So funktioniert es</h2>',
        "pt": '<h2 id="how-h">Como funciona</h2>',
        "it": '<h2 id="how-h">Come funziona</h2>',
        "zh": '<h2 id="how-h">工作原理</h2>',
        "hi": '<h2 id="how-h">यह कैसे काम करता है</h2>',
    },
    "step1_h3": {
        "en": '<h3>Install the sensor</h3>',
        "ar": '<h3>ثبّت المستشعر</h3>',
        "es": '<h3>Instala el sensor</h3>',
        "fr": '<h3>Installez le capteur</h3>',
        "de": '<h3>Sensor installieren</h3>',
        "pt": '<h3>Instale o sensor</h3>',
        "it": '<h3>Installa il sensore</h3>',
        "zh": '<h3>安装传感器</h3>',
        "hi": '<h3>सेंसर इंस्टॉल करें</h3>',
    },
    "step2_h3": {
        "en": '<h3>It reports to the hub</h3>',
        "ar": '<h3>يرسل تقاريره إلى الخادم المركزي</h3>',
        "es": '<h3>Informa al hub</h3>',
        "fr": '<h3>Il fait son rapport au hub</h3>',
        "de": '<h3>Er meldet sich beim Hub</h3>',
        "pt": '<h3>Ele reporta ao hub</h3>',
        "it": '<h3>Comunica con il hub</h3>',
        "zh": '<h3>它向中枢汇报</h3>',
        "hi": '<h3>यह हब को रिपोर्ट करता है</h3>',
    },
    "step3_h3": {
        "en": '<h3>You get a dashboard and an alert</h3>',
        "ar": '<h3>تحصل على لوحة تحكم وتنبيه</h3>',
        "es": '<h3>Obtienes un panel y una alerta</h3>',
        "fr": '<h3>Vous obtenez un tableau de bord et une alerte</h3>',
        "de": '<h3>Sie erhalten ein Dashboard und eine Benachrichtigung</h3>',
        "pt": '<h3>Você recebe um painel e um alerta</h3>',
        "it": '<h3>Ottieni una dashboard e un avviso</h3>',
        "zh": '<h3>你会得到一个仪表盘和一条提醒</h3>',
        "hi": '<h3>आपको एक डैशबोर्ड और एक अलर्ट मिलता है</h3>',
    },
    "fixes_eyebrow": {
        "en": '<p class="eyebrow">The difference</p>',
        "ar": '<p class="eyebrow">الفرق</p>',
        "es": '<p class="eyebrow">La diferencia</p>',
        "fr": '<p class="eyebrow">La différence</p>',
        "de": '<p class="eyebrow">Der Unterschied</p>',
        "pt": '<p class="eyebrow">A diferença</p>',
        "it": '<p class="eyebrow">La differenza</p>',
        "zh": '<p class="eyebrow">与众不同之处</p>',
        "hi": '<p class="eyebrow">यही अंतर है</p>',
    },
    "fixes_h2": {
        "en": '<h2 id="fixes-h">Every other tool sends you an alert.<br><span class="hl">Vault sends you the repair it already made.</span></h2>',
        "ar": '<h2 id="fixes-h">كل أداة أخرى ترسل لك تنبيهًا.<br><span class="hl">Vault يرسل لك الإصلاح الذي أجراه بالفعل.</span></h2>',
        "es": '<h2 id="fixes-h">Cualquier otra herramienta te envía una alerta.<br><span class="hl">Vault te envía la reparación que ya hizo.</span></h2>',
        "fr": '<h2 id="fixes-h">Tous les autres outils vous envoient une alerte.<br><span class="hl">Vault vous envoie la réparation qu\'il a déjà faite.</span></h2>',
        "de": '<h2 id="fixes-h">Jedes andere Tool schickt Ihnen eine Warnung.<br><span class="hl">Vault schickt Ihnen die Reparatur, die es bereits vorgenommen hat.</span></h2>',
        "pt": '<h2 id="fixes-h">Todas as outras ferramentas enviam-lhe um alerta.<br><span class="hl">O Vault envia-lhe o conserto que já fez.</span></h2>',
        "it": '<h2 id="fixes-h">Ogni altro strumento ti manda un avviso.<br><span class="hl">Vault ti manda la riparazione che ha già fatto.</span></h2>',
        "zh": '<h2 id="fixes-h">其他所有工具只会给你发一条警报。<br><span class="hl">Vault 发给你的，是它已经完成的修复。</span></h2>',
        "hi": '<h2 id="fixes-h">हर दूसरा टूल आपको सिर्फ़ एक अलर्ट भेजता है।<br><span class="hl">Vault आपको वह मरम्मत भेजता है जो वह पहले ही कर चुका है।</span></h2>',
    },
    "fcard1_h3": {
        "en": '<h3>A fixed list of repairs, not a shell</h3>',
        "ar": '<h3>قائمة إصلاحات ثابتة، لا واجهة أوامر مفتوحة</h3>',
        "es": '<h3>Una lista fija de reparaciones, no una terminal</h3>',
        "fr": '<h3>Une liste fixe de réparations, pas un shell</h3>',
        "de": '<h3>Eine feste Liste von Reparaturen, keine Shell</h3>',
        "pt": '<h3>Uma lista fixa de reparos, não um terminal</h3>',
        "it": '<h3>Un elenco fisso di riparazioni, non una shell</h3>',
        "zh": '<h3>一份固定的修复清单，而不是一个可任意执行的终端</h3>',
        "hi": '<h3>मरम्मत की एक तय सूची, कोई खुला शेल नहीं</h3>',
    },
    "fcard2_h3": {
        "en": '<h3>Every repair has a cooldown</h3>',
        "ar": '<h3>لكل إصلاح فترة تهدئة</h3>',
        "es": '<h3>Cada reparación tiene un tiempo de espera</h3>',
        "fr": '<h3>Chaque réparation a un temps de repos</h3>',
        "de": '<h3>Jede Reparatur hat eine Abklingzeit</h3>',
        "pt": '<h3>Cada reparo tem um tempo de espera</h3>',
        "it": '<h3>Ogni riparazione ha un periodo di attesa</h3>',
        "zh": '<h3>每次修复都有冷却时间</h3>',
        "hi": '<h3>हर मरम्मत के बाद एक कूलडाउन होता है</h3>',
    },
    "fcard3_h3": {
        "en": '<h3>Tell it what to do, in words</h3>',
        "ar": '<h3>أخبره بما يجب فعله، بكلماتك</h3>',
        "es": '<h3>Dile qué hacer, con palabras</h3>',
        "fr": '<h3>Dites-lui quoi faire, avec des mots</h3>',
        "de": '<h3>Sagen Sie ihm in Worten, was zu tun ist</h3>',
        "pt": '<h3>Diga-lhe o que fazer, em palavras</h3>',
        "it": '<h3>Digli cosa fare, a parole</h3>',
        "zh": '<h3>用文字告诉它该做什么</h3>',
        "hi": '<h3>उसे बताएं क्या करना है, शब्दों में</h3>',
    },
    "fcard4_h3": {
        "en": '<h3>It asks first when it matters</h3>',
        "ar": '<h3>يسأل أولًا عندما يكون الأمر مهمًا</h3>',
        "es": '<h3>Pregunta primero cuando importa</h3>',
        "fr": '<h3>Il demande d\'abord quand ça compte</h3>',
        "de": '<h3>Es fragt vorher, wenn es wichtig ist</h3>',
        "pt": '<h3>Ele pergunta primeiro quando importa</h3>',
        "it": '<h3>Chiede prima, quando è importante</h3>',
        "zh": '<h3>关键时刻，它会先询问</h3>',
        "hi": '<h3>जब बात अहम हो, तो यह पहले पूछता है</h3>',
    },
})

LITERAL.update({
    "pricing_h2": {
        'en': '<h2 id="pricing-h">Three months free.<br><span class="hl">No plans to choose, no card to enter.</span></h2>',
        'ar': '<h2 id="pricing-h">ثلاثة أشهر مجانًا.<br><span class="hl">لا خطة تختارها، ولا بطاقة تدخلها.</span></h2>',
        'es': '<h2 id="pricing-h">Tres meses gratis.<br><span class="hl">Sin planes que elegir, sin tarjeta que introducir.</span></h2>',
        'fr': '<h2 id="pricing-h">Trois mois gratuits.<br><span class="hl">Aucune offre à choisir, aucune carte à saisir.</span></h2>',
        'de': '<h2 id="pricing-h">Drei Monate kostenlos.<br><span class="hl">Kein Plan zu wählen, keine Karte einzugeben.</span></h2>',
        'pt': '<h2 id="pricing-h">Três meses grátis.<br><span class="hl">Sem planos para escolher, sem cartão para inserir.</span></h2>',
        'it': '<h2 id="pricing-h">Tre mesi gratis.<br><span class="hl">Nessun piano da scegliere, nessuna carta da inserire.</span></h2>',
        'zh': '<h2 id="pricing-h">三个月免费。<br><span class="hl">无套餐可选，无需输入银行卡。</span></h2>',
        'hi': '<h2 id="pricing-h">तीन महीने मुफ़्त।<br><span class="hl">कोई प्लान चुनना नहीं, कोई कार्ड डालना नहीं।</span></h2>',
    },
    "faq_h2": {
        "en": '<h2 id="faq-h">Questions worth asking first</h2>',
        "ar": '<h2 id="faq-h">أسئلة يستحق طرحها أولًا</h2>',
        "es": '<h2 id="faq-h">Preguntas que conviene hacer primero</h2>',
        "fr": '<h2 id="faq-h">Des questions à poser d\'abord</h2>',
        "de": '<h2 id="faq-h">Fragen, die man vorher stellen sollte</h2>',
        "pt": '<h2 id="faq-h">Perguntas que vale a pena fazer primeiro</h2>',
        "it": '<h2 id="faq-h">Domande da farsi prima</h2>',
        "zh": '<h2 id="faq-h">值得先问的问题</h2>',
        "hi": '<h2 id="faq-h">पहले पूछे जाने लायक सवाल</h2>',
    },
    "access_h2": {
        'en': '<h2 id="access-h">Start your three months</h2>',
        'ar': '<h2 id="access-h">ابدأ أشهرك الثلاثة</h2>',
        'es': '<h2 id="access-h">Empieza tus tres meses</h2>',
        'fr': '<h2 id="access-h">Démarrez vos trois mois</h2>',
        'de': '<h2 id="access-h">Starten Sie Ihre drei Monate</h2>',
        'pt': '<h2 id="access-h">Comece seus três meses</h2>',
        'it': '<h2 id="access-h">Inizia i tuoi tre mesi</h2>',
        'zh': '<h2 id="access-h">开始你的三个月</h2>',
        'hi': '<h2 id="access-h">अपने तीन महीने शुरू करें</h2>',
    },
})

LITERAL.update({
    "faq1_q": {
        "en": '<summary>What can the sensor actually see?</summary>',
        "ar": '<summary>ما الذي يمكن للمستشعر رؤيته فعليًا؟</summary>',
        "es": '<summary>¿Qué puede ver realmente el sensor?</summary>',
        "fr": '<summary>Que peut réellement voir le capteur ?</summary>',
        "de": '<summary>Was kann der Sensor tatsächlich sehen?</summary>',
        "pt": '<summary>O que o sensor consegue realmente ver?</summary>',
        "it": '<summary>Cosa può effettivamente vedere il sensore?</summary>',
        "zh": '<summary>传感器到底能看到什么？</summary>',
        "hi": '<summary>सेंसर वास्तव में क्या देख सकता है?</summary>',
    },
    "faq2_q": {
        "en": '<summary>Where does the data live?</summary>',
        "ar": '<summary>أين تُخزَّن البيانات؟</summary>',
        "es": '<summary>¿Dónde viven los datos?</summary>',
        "fr": '<summary>Où vivent les données ?</summary>',
        "de": '<summary>Wo liegen die Daten?</summary>',
        "pt": '<summary>Onde ficam os dados?</summary>',
        "it": '<summary>Dove vivono i dati?</summary>',
        "zh": '<summary>数据存放在哪里？</summary>',
        "hi": '<summary>डेटा कहाँ रहता है?</summary>',
    },
    "faq3_q": {
        "en": '<summary>What happens on my network if the hub is unreachable?</summary>',
        "ar": '<summary>ماذا يحدث على شبكتي إذا تعذّر الوصول إلى الخادم المركزي؟</summary>',
        "es": '<summary>¿Qué pasa en mi red si el hub no está disponible?</summary>',
        "fr": '<summary>Que se passe-t-il sur mon réseau si le hub est inaccessible ?</summary>',
        "de": '<summary>Was passiert in meinem Netzwerk, wenn der Hub nicht erreichbar ist?</summary>',
        "pt": '<summary>O que acontece na minha rede se o hub estiver inacessível?</summary>',
        "it": '<summary>Cosa succede sulla mia rete se il hub è irraggiungibile?</summary>',
        "zh": '<summary>如果中枢无法访问，我的网络会怎样？</summary>',
        "hi": '<summary>अगर हब तक पहुँच न हो, तो मेरे नेटवर्क पर क्या होगा?</summary>',
    },
    "faq4_q": {
        'en': '<summary>What does it cost?</summary>',
        'ar': '<summary>كم يكلّف؟</summary>',
        'es': '<summary>¿Cuánto cuesta?</summary>',
        'fr': '<summary>Combien ça coûte ?</summary>',
        'de': '<summary>Was kostet es?</summary>',
        'pt': '<summary>Quanto custa?</summary>',
        'it': '<summary>Quanto costa?</summary>',
        'zh': '<summary>需要多少钱？</summary>',
        'hi': '<summary>इसकी कीमत क्या है?</summary>',
    },
    "faq5_q": {
        'en': '<summary>How do I cancel?</summary>',
        'ar': '<summary>كيف أُلغي؟</summary>',
        'es': '<summary>¿Cómo cancelo?</summary>',
        'fr': '<summary>Comment résilier ?</summary>',
        'de': '<summary>Wie kündige ich?</summary>',
        'pt': '<summary>Como cancelo?</summary>',
        'it': '<summary>Come disdico?</summary>',
        'zh': '<summary>如何取消？</summary>',
        'hi': '<summary>मैं कैसे रद्द करूँ?</summary>',
    },
})

LITERAL.update({
    "foot_tag": {
        "en": '<p class="foot-tag">The watchdog that fixes the routine stuff itself.</p>',
        "ar": '<p class="foot-tag">حارس الشبكة الذي يصلح الأمور الروتينية بنفسه.</p>',
        "es": '<p class="foot-tag">El vigilante que resuelve por su cuenta lo rutinario.</p>',
        "fr": '<p class="foot-tag">Le gardien qui règle lui-même les problèmes courants.</p>',
        "de": '<p class="foot-tag">Der Wächter, der die Routineprobleme selbst behebt.</p>',
        "pt": '<p class="foot-tag">O vigilante que resolve por conta própria as rotinas.</p>',
        "it": '<p class="foot-tag">Il guardiano che risolve da solo le cose di routine.</p>',
        "zh": '<p class="foot-tag">这只看门狗会自己搞定日常琐事。</p>',
        "hi": '<p class="foot-tag">यह वॉचडॉग रोज़मर्रा की चीज़ें खुद ठीक कर देता है।</p>',
    },
    "foot_h3_product": {
        "en": '<h3>Product</h3>',
        "ar": '<h3>المنتج</h3>',
        "es": '<h3>Producto</h3>',
        "fr": '<h3>Produit</h3>',
        "de": '<h3>Produkt</h3>',
        "pt": '<h3>Produto</h3>',
        "it": '<h3>Prodotto</h3>',
        "zh": '<h3>产品</h3>',
        "hi": '<h3>प्रोडक्ट</h3>',
    },
    "foot_h3_developers": {
        "en": '<h3>Developers</h3>',
        "ar": '<h3>المطوّرون</h3>',
        "es": '<h3>Desarrolladores</h3>',
        "fr": '<h3>Développeurs</h3>',
        "de": '<h3>Entwickler</h3>',
        "pt": '<h3>Desenvolvedores</h3>',
        "it": '<h3>Sviluppatori</h3>',
        "zh": '<h3>开发者</h3>',
        "hi": '<h3>डेवलपर्स</h3>',
    },
    "foot_h3_support": {
        "en": '<h3>Support</h3>',
        "ar": '<h3>الدعم</h3>',
        "es": '<h3>Soporte</h3>',
        "fr": '<h3>Assistance</h3>',
        "de": '<h3>Support</h3>',
        "pt": '<h3>Suporte</h3>',
        "it": '<h3>Assistenza</h3>',
        "zh": '<h3>支持</h3>',
        "hi": '<h3>सहायता</h3>',
    },
    "foot_h3_account": {
        "en": '<h3>Account</h3>',
        "ar": '<h3>الحساب</h3>',
        "es": '<h3>Cuenta</h3>',
        "fr": '<h3>Compte</h3>',
        "de": '<h3>Konto</h3>',
        "pt": '<h3>Conta</h3>',
        "it": '<h3>Account</h3>',
        "zh": '<h3>账户</h3>',
        "hi": '<h3>अकाउंट</h3>',
    },
    "foot_link_watches": {
        "en": '<a href="#watches">What it watches</a>',
        "ar": '<a href="#watches">ما الذي يراقبه</a>',
        "es": '<a href="#watches">Qué vigila</a>',
        "fr": '<a href="#watches">Ce qu\'il surveille</a>',
        "de": '<a href="#watches">Was es überwacht</a>',
        "pt": '<a href="#watches">O que ele vigia</a>',
        "it": '<a href="#watches">Cosa sorveglia</a>',
        "zh": '<a href="#watches">它监控什么</a>',
        "hi": '<a href="#watches">यह क्या देखता है</a>',
    },
    "foot_link_how": {
        "en": '<a href="#how">How it works</a>',
        "ar": '<a href="#how">كيف يعمل</a>',
        "es": '<a href="#how">Cómo funciona</a>',
        "fr": '<a href="#how">Comment ça marche</a>',
        "de": '<a href="#how">So funktioniert es</a>',
        "pt": '<a href="#how">Como funciona</a>',
        "it": '<a href="#how">Come funziona</a>',
        "zh": '<a href="#how">工作原理</a>',
        "hi": '<a href="#how">यह कैसे काम करता है</a>',
    },
    "foot_link_pricing": {
        'en': '<a href="#pricing">Free trial</a>',
        'ar': '<a href="#pricing">تجربة مجانية</a>',
        'es': '<a href="#pricing">Prueba gratis</a>',
        'fr': '<a href="#pricing">Essai gratuit</a>',
        'de': '<a href="#pricing">Kostenlos testen</a>',
        'pt': '<a href="#pricing">Teste grátis</a>',
        'it': '<a href="#pricing">Prova gratuita</a>',
        'zh': '<a href="#pricing">免费试用</a>',
        'hi': '<a href="#pricing">मुफ़्त ट्रायल</a>',
    },
    "foot_link_api": {
        "en": '<a href="https://api.guarder.io/v1/docs">API reference</a>',
        "ar": '<a href="https://api.guarder.io/v1/docs">مرجع واجهة برمجة التطبيقات (API)</a>',
        "es": '<a href="https://api.guarder.io/v1/docs">Referencia de la API</a>',
        "fr": '<a href="https://api.guarder.io/v1/docs">Référence de l\'API</a>',
        "de": '<a href="https://api.guarder.io/v1/docs">API-Referenz</a>',
        "pt": '<a href="https://api.guarder.io/v1/docs">Referência da API</a>',
        "it": '<a href="https://api.guarder.io/v1/docs">Riferimento API</a>',
        "zh": '<a href="https://api.guarder.io/v1/docs">API 参考文档</a>',
        "hi": '<a href="https://api.guarder.io/v1/docs">API संदर्भ</a>',
    },
    "foot_link_help": {
        "en": '<a href="https://my.guarder.io/help">Help guides</a>',
        "ar": '<a href="https://my.guarder.io/help">دليل المساعدة</a>',
        "es": '<a href="https://my.guarder.io/help">Guías de ayuda</a>',
        "fr": '<a href="https://my.guarder.io/help">Guides d\'aide</a>',
        "de": '<a href="https://my.guarder.io/help">Hilfe-Anleitungen</a>',
        "pt": '<a href="https://my.guarder.io/help">Guias de ajuda</a>',
        "it": '<a href="https://my.guarder.io/help">Guide di aiuto</a>',
        "zh": '<a href="https://my.guarder.io/help">帮助指南</a>',
        "hi": '<a href="https://my.guarder.io/help">सहायता गाइड</a>',
    },
    "foot_link_access": {
        "en": '<a href="/signup.html">Request access</a>',
        "ar": '<a href="/signup.html">طلب الوصول</a>',
        "es": '<a href="/signup.html">Solicitar acceso</a>',
        "fr": '<a href="/signup.html">Demander un accès</a>',
        "de": '<a href="/signup.html">Zugang anfragen</a>',
        "pt": '<a href="/signup.html">Solicitar acesso</a>',
        "it": '<a href="/signup.html">Richiedi accesso</a>',
        "zh": '<a href="/signup.html">申请访问权限</a>',
        "hi": '<a href="/signup.html">एक्सेस के लिए आवेदन करें</a>',
    },
    "foot_link_terms": {
        "en": '<a href="/terms.html">Terms</a>',
        "ar": '<a href="/terms.html">الشروط</a>',
        "es": '<a href="/terms.html">Términos</a>',
        "fr": '<a href="/terms.html">Conditions</a>',
        "de": '<a href="/terms.html">Bedingungen</a>',
        "pt": '<a href="/terms.html">Termos</a>',
        "it": '<a href="/terms.html">Termini</a>',
        "zh": '<a href="/terms.html">条款</a>',
        "hi": '<a href="/terms.html">शर्तें</a>',
    },
    "foot_copy": {
        "en": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; network watchdog</p>',
        "ar": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; حارس الشبكة</p>',
        "es": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; vigilante de redes</p>',
        "fr": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; veille de réseau</p>',
        "de": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; Netzwerk-Wächter</p>',
        "pt": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; vigilante de rede</p>',
        "it": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; guardiano di rete</p>',
        "zh": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; 网络看门狗</p>',
        "hi": '<p class="foot-copy">&copy; <span id="copyright-year">2026</span> Vault &mdash; नेटवर्क वॉचडॉग</p>',
    },
})

# ---------------------------------------------------------------------------
# The capabilities / evidence / who / integrations sections. Every string in
# these four sections is a single line in site/index.html, so all of them are
# LITERAL entries -- none needed ONE_OFF. Two things travel inside the text
# and must survive translation: the <span class="hl"> in each section's <h2>
# (the highlighted half of the headline) and the <span class="tag">demo
# </span> in each chart-sub (the label saying the chart is NOT live data).
# Keep exactly one of those spans per string; the split point naturally falls
# on different words in each language. The capability switcher copy and the
# chart axis labels are drawn by site/app.js and are NOT translated here.
# ---------------------------------------------------------------------------

LITERAL.update({
    "nav_caps": {
        "en": '<a class="jump jump-new" href="#caps">Capabilities</a>',
        "ar": '<a class="jump jump-new" href="#caps">القدرات</a>',
        "es": '<a class="jump jump-new" href="#caps">Capacidades</a>',
        "fr": '<a class="jump jump-new" href="#caps">Fonctions</a>',
        "de": '<a class="jump jump-new" href="#caps">Funktionen</a>',
        "pt": '<a class="jump jump-new" href="#caps">Capacidades</a>',
        "it": '<a class="jump jump-new" href="#caps">Funzioni</a>',
        "zh": '<a class="jump jump-new" href="#caps">功能</a>',
        "hi": '<a class="jump jump-new" href="#caps">क्षमताएँ</a>',
    },
    "nav_integrations": {
        "en": '<a class="jump jump-new" href="#integrations">Integrations</a>',
        "ar": '<a class="jump jump-new" href="#integrations">التكاملات</a>',
        "es": '<a class="jump jump-new" href="#integrations">Integraciones</a>',
        "fr": '<a class="jump jump-new" href="#integrations">Intégrations</a>',
        "de": '<a class="jump jump-new" href="#integrations">Integrationen</a>',
        "pt": '<a class="jump jump-new" href="#integrations">Integrações</a>',
        "it": '<a class="jump jump-new" href="#integrations">Integrazioni</a>',
        "zh": '<a class="jump jump-new" href="#integrations">集成</a>',
        "hi": '<a class="jump jump-new" href="#integrations">इंटीग्रेशन</a>',
    },
    "caps_eyebrow": {
        "en": '<p class="eyebrow" id="caps-eyebrow">What it does</p>',
        "ar": '<p class="eyebrow" id="caps-eyebrow">ماذا يفعل</p>',
        "es": '<p class="eyebrow" id="caps-eyebrow">Qué hace</p>',
        "fr": '<p class="eyebrow" id="caps-eyebrow">Ce qu\'il fait</p>',
        "de": '<p class="eyebrow" id="caps-eyebrow">Was es tut</p>',
        "pt": '<p class="eyebrow" id="caps-eyebrow">O que faz</p>',
        "it": '<p class="eyebrow" id="caps-eyebrow">Cosa fa</p>',
        "zh": '<p class="eyebrow" id="caps-eyebrow">它做什么</p>',
        "hi": '<p class="eyebrow" id="caps-eyebrow">यह क्या करता है</p>',
    },
    "caps_h": {
        "en": '<h2 id="caps-h">Six jobs, one sensor, <span class="hl">one page.</span></h2>',
        "ar": '<h2 id="caps-h">ست مهام، مستشعر واحد، <span class="hl">صفحة واحدة.</span></h2>',
        "es": '<h2 id="caps-h">Seis tareas, un sensor, <span class="hl">una sola página.</span></h2>',
        "fr": '<h2 id="caps-h">Six tâches, un capteur, <span class="hl">une seule page.</span></h2>',
        "de": '<h2 id="caps-h">Sechs Aufgaben, ein Sensor, <span class="hl">eine Seite.</span></h2>',
        "pt": '<h2 id="caps-h">Seis tarefas, um sensor, <span class="hl">uma única página.</span></h2>',
        "it": '<h2 id="caps-h">Sei compiti, un sensore, <span class="hl">una sola pagina.</span></h2>',
        "zh": '<h2 id="caps-h">六项任务，一个传感器，<span class="hl">一个页面。</span></h2>',
        "hi": '<h2 id="caps-h">छह काम, एक सेंसर, <span class="hl">एक ही पेज।</span></h2>',
    },
    "caps_lede": {
        "en": '<p class="cap-lede" id="caps-lede">Pick one and the panel shows it running. These are the checks Vault performs on every sweep, and what it does when one of them fails.</p>',
        "ar": '<p class="cap-lede" id="caps-lede">اختر واحدة وستعرض اللوحة تنفيذها. هذه هي الفحوص التي يجريها Vault في كل جولة، وما يفعله عندما يفشل أحدها.</p>',
        "es": '<p class="cap-lede" id="caps-lede">Elige una y el panel la muestra en marcha. Estas son las comprobaciones que Vault realiza en cada barrido, y lo que hace cuando alguna falla.</p>',
        "fr": '<p class="cap-lede" id="caps-lede">Choisissez-en une et le panneau la montre en action. Voici les vérifications que Vault effectue à chaque passage, et ce qu\'il fait quand l\'une d\'elles échoue.</p>',
        "de": '<p class="cap-lede" id="caps-lede">Wählen Sie eine aus, und das Panel zeigt sie in Aktion. Das sind die Prüfungen, die Vault bei jedem Durchlauf ausführt, und was es tut, wenn eine davon fehlschlägt.</p>',
        "pt": '<p class="cap-lede" id="caps-lede">Escolha uma e o painel mostra-a em execução. Estas são as verificações que o Vault faz em cada varrimento, e o que faz quando uma delas falha.</p>',
        "it": '<p class="cap-lede" id="caps-lede">Scegline una e il pannello la mostra in funzione. Questi sono i controlli che Vault esegue a ogni scansione, e cosa fa quando uno di essi fallisce.</p>',
        "zh": '<p class="cap-lede" id="caps-lede">选择一项，面板就会展示它的运行过程。这些是 Vault 每次巡检执行的检查，以及其中一项失败时它会怎么做。</p>',
        "hi": '<p class="cap-lede" id="caps-lede">किसी एक को चुनें और पैनल उसे चलते हुए दिखाएगा। ये वे जाँचें हैं जो Vault हर स्वीप में करता है, और इनमें से कोई विफल होने पर वह क्या करता है।</p>',
    },
    "evidence_eyebrow": {
        "en": '<p class="eyebrow" id="evidence-eyebrow">Evidence</p>',
        "ar": '<p class="eyebrow" id="evidence-eyebrow">الدليل</p>',
        "es": '<p class="eyebrow" id="evidence-eyebrow">Evidencia</p>',
        "fr": '<p class="eyebrow" id="evidence-eyebrow">Preuves</p>',
        "de": '<p class="eyebrow" id="evidence-eyebrow">Belege</p>',
        "pt": '<p class="eyebrow" id="evidence-eyebrow">Evidência</p>',
        "it": '<p class="eyebrow" id="evidence-eyebrow">Prove</p>',
        "zh": '<p class="eyebrow" id="evidence-eyebrow">证据</p>',
        "hi": '<p class="eyebrow" id="evidence-eyebrow">प्रमाण</p>',
    },
    "evidence_h": {
        "en": '<h2 id="evidence-h">Detection is the easy half. <span class="hl">This is the other half.</span></h2>',
        "ar": '<h2 id="evidence-h">الاكتشاف هو النصف السهل. <span class="hl">وهذا هو النصف الآخر.</span></h2>',
        "es": '<h2 id="evidence-h">Detectar es la mitad fácil. <span class="hl">Esta es la otra mitad.</span></h2>',
        "fr": '<h2 id="evidence-h">La détection, c\'est la moitié facile. <span class="hl">Voici l\'autre moitié.</span></h2>',
        "de": '<h2 id="evidence-h">Erkennen ist die einfache Hälfte. <span class="hl">Das hier ist die andere.</span></h2>',
        "pt": '<h2 id="evidence-h">Detetar é a metade fácil. <span class="hl">Esta é a outra metade.</span></h2>',
        "it": '<h2 id="evidence-h">Rilevare è la metà facile. <span class="hl">Questa è l\'altra metà.</span></h2>',
        "zh": '<h2 id="evidence-h">发现问题是容易的那一半。<span class="hl">这是另一半。</span></h2>',
        "hi": '<h2 id="evidence-h">पता लगाना आसान आधा हिस्सा है। <span class="hl">यह रहा दूसरा आधा।</span></h2>',
    },
    "evidence_lede": {
        "en": '<p class="cap-lede" id="evidence-lede">Every tool can raise an alert. The number that matters is how much of it never reached you at all.</p>',
        "ar": '<p class="cap-lede" id="evidence-lede">كل أداة تستطيع إطلاق تنبيه. الرقم المهم هو كم من ذلك لم يصل إليك أصلاً.</p>',
        "es": '<p class="cap-lede" id="evidence-lede">Cualquier herramienta puede lanzar una alerta. La cifra que importa es cuánto de todo eso nunca llegó a ti.</p>',
        "fr": '<p class="cap-lede" id="evidence-lede">N\'importe quel outil sait déclencher une alerte. Le chiffre qui compte, c\'est tout ce qui ne vous est jamais parvenu.</p>',
        "de": '<p class="cap-lede" id="evidence-lede">Einen Alarm auslösen kann jedes Werkzeug. Die Zahl, auf die es ankommt, ist, wie viel davon Sie nie erreicht hat.</p>',
        "pt": '<p class="cap-lede" id="evidence-lede">Qualquer ferramenta consegue lançar um alerta. O número que importa é quanto disso nunca chegou até si.</p>',
        "it": '<p class="cap-lede" id="evidence-lede">Qualsiasi strumento sa lanciare un avviso. Il numero che conta è quanto di tutto ciò non ti è mai arrivato.</p>',
        "zh": '<p class="cap-lede" id="evidence-lede">任何工具都会发警报。真正重要的数字，是其中有多少根本没有惊动你。</p>',
        "hi": '<p class="cap-lede" id="evidence-lede">अलर्ट तो कोई भी टूल दे सकता है। मायने यह रखता है कि उसमें से कितना आप तक पहुँचा ही नहीं।</p>',
    },
    "chart1_h": {
        "en": '<h3 id="chart1-h">Change to repair</h3>',
        "ar": '<h3 id="chart1-h">من التغيير إلى الإصلاح</h3>',
        "es": '<h3 id="chart1-h">Del cambio a la reparación</h3>',
        "fr": '<h3 id="chart1-h">Du changement à la réparation</h3>',
        "de": '<h3 id="chart1-h">Von der Änderung zur Reparatur</h3>',
        "pt": '<h3 id="chart1-h">Da alteração à reparação</h3>',
        "it": '<h3 id="chart1-h">Dal cambiamento alla riparazione</h3>',
        "zh": '<h3 id="chart1-h">从变化到修复</h3>',
        "hi": '<h3 id="chart1-h">बदलाव से मरम्मत तक</h3>',
    },
    "chart1_sub": {
        "en": '<p class="chart-sub" id="chart1-sub">Minutes from finding to fix <span class="tag">demo</span></p>',
        "ar": '<p class="chart-sub" id="chart1-sub">دقائق من الاكتشاف إلى الإصلاح <span class="tag">تجريبي</span></p>',
        "es": '<p class="chart-sub" id="chart1-sub">Minutos desde el hallazgo hasta la solución <span class="tag">demo</span></p>',
        "fr": '<p class="chart-sub" id="chart1-sub">Minutes entre la détection et la correction <span class="tag">démo</span></p>',
        "de": '<p class="chart-sub" id="chart1-sub">Minuten vom Fund bis zur Behebung <span class="tag">Demo</span></p>',
        "pt": '<p class="chart-sub" id="chart1-sub">Minutos entre a deteção e a correção <span class="tag">exemplo</span></p>',
        "it": '<p class="chart-sub" id="chart1-sub">Minuti dalla rilevazione alla correzione <span class="tag">demo</span></p>',
        "zh": '<p class="chart-sub" id="chart1-sub">从发现到修复的分钟数 <span class="tag">演示</span></p>',
        "hi": '<p class="chart-sub" id="chart1-sub">पता चलने से ठीक होने तक के मिनट <span class="tag">डेमो</span></p>',
    },
    "chart2_h": {
        "en": '<h3 id="chart2-h">Handled or escalated</h3>',
        "ar": '<h3 id="chart2-h">عولج أم صُعّد</h3>',
        "es": '<h3 id="chart2-h">Resuelto o escalado</h3>',
        "fr": '<h3 id="chart2-h">Traité ou escaladé</h3>',
        "de": '<h3 id="chart2-h">Erledigt oder eskaliert</h3>',
        "pt": '<h3 id="chart2-h">Resolvido ou escalado</h3>',
        "it": '<h3 id="chart2-h">Risolto o inoltrato</h3>',
        "zh": '<h3 id="chart2-h">自行处理还是上报</h3>',
        "hi": '<h3 id="chart2-h">खुद निपटाया या आगे भेजा</h3>',
    },
    "chart2_sub": {
        "en": '<p class="chart-sub" id="chart2-sub">Closed by Vault, by day <span class="tag">demo</span></p>',
        "ar": '<p class="chart-sub" id="chart2-sub">ما أغلقه Vault، يوماً بيوم <span class="tag">تجريبي</span></p>',
        "es": '<p class="chart-sub" id="chart2-sub">Cerrado por Vault, por día <span class="tag">demo</span></p>',
        "fr": '<p class="chart-sub" id="chart2-sub">Clôturé par Vault, par jour <span class="tag">démo</span></p>',
        "de": '<p class="chart-sub" id="chart2-sub">Von Vault geschlossen, pro Tag <span class="tag">Demo</span></p>',
        "pt": '<p class="chart-sub" id="chart2-sub">Fechado pelo Vault, por dia <span class="tag">exemplo</span></p>',
        "it": '<p class="chart-sub" id="chart2-sub">Chiuso da Vault, per giorno <span class="tag">demo</span></p>',
        "zh": '<p class="chart-sub" id="chart2-sub">Vault 每日关闭的数量 <span class="tag">演示</span></p>',
        "hi": '<p class="chart-sub" id="chart2-sub">Vault द्वारा बंद किए गए, प्रतिदिन <span class="tag">डेमो</span></p>',
    },
    "chart3_h": {
        "en": '<h3 id="chart3-h">What it found</h3>',
        "ar": '<h3 id="chart3-h">ما الذي وجده</h3>',
        "es": '<h3 id="chart3-h">Qué encontró</h3>',
        "fr": '<h3 id="chart3-h">Ce qu\'il a trouvé</h3>',
        "de": '<h3 id="chart3-h">Was es gefunden hat</h3>',
        "pt": '<h3 id="chart3-h">O que encontrou</h3>',
        "it": '<h3 id="chart3-h">Cosa ha trovato</h3>',
        "zh": '<h3 id="chart3-h">它发现了什么</h3>',
        "hi": '<h3 id="chart3-h">उसे क्या मिला</h3>',
    },
    "chart3_sub": {
        "en": '<p class="chart-sub" id="chart3-sub">By severity, fourteen days <span class="tag">demo</span></p>',
        "ar": '<p class="chart-sub" id="chart3-sub">حسب الخطورة، أربعة عشر يوماً <span class="tag">تجريبي</span></p>',
        "es": '<p class="chart-sub" id="chart3-sub">Por gravedad, catorce días <span class="tag">demo</span></p>',
        "fr": '<p class="chart-sub" id="chart3-sub">Par gravité, quatorze jours <span class="tag">démo</span></p>',
        "de": '<p class="chart-sub" id="chart3-sub">Nach Schweregrad, vierzehn Tage <span class="tag">Demo</span></p>',
        "pt": '<p class="chart-sub" id="chart3-sub">Por gravidade, catorze dias <span class="tag">exemplo</span></p>',
        "it": '<p class="chart-sub" id="chart3-sub">Per gravità, quattordici giorni <span class="tag">demo</span></p>',
        "zh": '<p class="chart-sub" id="chart3-sub">按严重程度，十四天 <span class="tag">演示</span></p>',
        "hi": '<p class="chart-sub" id="chart3-sub">गंभीरता के अनुसार, चौदह दिन <span class="tag">डेमो</span></p>',
    },
    "who_eyebrow": {
        "en": '<p class="eyebrow" id="who-eyebrow">Who uses it</p>',
        "ar": '<p class="eyebrow" id="who-eyebrow">من يستخدمه</p>',
        "es": '<p class="eyebrow" id="who-eyebrow">Quién lo usa</p>',
        "fr": '<p class="eyebrow" id="who-eyebrow">Qui l\'utilise</p>',
        "de": '<p class="eyebrow" id="who-eyebrow">Wer es nutzt</p>',
        "pt": '<p class="eyebrow" id="who-eyebrow">Quem o usa</p>',
        "it": '<p class="eyebrow" id="who-eyebrow">Chi lo usa</p>',
        "zh": '<p class="eyebrow" id="who-eyebrow">谁在使用</p>',
        "hi": '<p class="eyebrow" id="who-eyebrow">इसे कौन इस्तेमाल करता है</p>',
    },
    "who_h": {
        "en": '<h2 id="who-h">Built for whoever <span class="hl">gets the call.</span></h2>',
        "ar": '<h2 id="who-h">مصنوع لكل من <span class="hl">تصله المكالمة.</span></h2>',
        "es": '<h2 id="who-h">Hecho para quien <span class="hl">recibe la llamada.</span></h2>',
        "fr": '<h2 id="who-h">Conçu pour celui qui <span class="hl">reçoit l\'appel.</span></h2>',
        "de": '<h2 id="who-h">Gemacht für alle, die <span class="hl">den Anruf bekommen.</span></h2>',
        "pt": '<h2 id="who-h">Feito para quem <span class="hl">recebe a chamada.</span></h2>',
        "it": '<h2 id="who-h">Fatto per chi <span class="hl">riceve la chiamata.</span></h2>',
        "zh": '<h2 id="who-h">为接到那通电话的人<span class="hl">而造。</span></h2>',
        "hi": '<h2 id="who-h">उनके लिए बना <span class="hl">जिन्हें कॉल आती है।</span></h2>',
    },
    "who_lede": {
        "en": '<p class="cap-lede" id="who-lede">The same sensor and the same page, whether you are looking after one house or a hundred client sites.</p>',
        "ar": '<p class="cap-lede" id="who-lede">المستشعر نفسه والصفحة نفسها، سواء كنت تعتني بمنزل واحد أو بمئة موقع لعملاء.</p>',
        "es": '<p class="cap-lede" id="who-lede">El mismo sensor y la misma página, tanto si cuidas de una casa como de cien sitios de clientes.</p>',
        "fr": '<p class="cap-lede" id="who-lede">Le même capteur et la même page, que vous veilliez sur une maison ou sur cent sites clients.</p>',
        "de": '<p class="cap-lede" id="who-lede">Derselbe Sensor und dieselbe Seite, ob Sie sich um ein Haus oder um hundert Kundenstandorte kümmern.</p>',
        "pt": '<p class="cap-lede" id="who-lede">O mesmo sensor e a mesma página, quer cuide de uma casa ou de cem sites de clientes.</p>',
        "it": '<p class="cap-lede" id="who-lede">Lo stesso sensore e la stessa pagina, che tu segua una casa o cento sedi di clienti.</p>',
        "zh": '<p class="cap-lede" id="who-lede">无论你照看的是一个家还是一百个客户站点，都是同一个传感器、同一个页面。</p>',
        "hi": '<p class="cap-lede" id="who-lede">वही सेंसर और वही पेज, चाहे आप एक घर संभाल रहे हों या सौ क्लाइंट साइटें।</p>',
    },
    "who1_h": {
        "en": '<h3 id="who1-h">People already doing this</h3>',
        "ar": '<h3 id="who1-h">من يقومون بهذا العمل أصلاً</h3>',
        "es": '<h3 id="who1-h">Quienes ya se dedican a esto</h3>',
        "fr": '<h3 id="who1-h">Ceux qui font déjà ce métier</h3>',
        "de": '<h3 id="who1-h">Menschen, die das ohnehin schon tun</h3>',
        "pt": '<h3 id="who1-h">Quem já faz isto</h3>',
        "it": '<h3 id="who1-h">Chi lo fa già di mestiere</h3>',
        "zh": '<h3 id="who1-h">已经在做这件事的人</h3>',
        "hi": '<h3 id="who1-h">जो पहले से यह काम कर रहे हैं</h3>',
    },
    "who1_b": {
        "en": '<p class="who-body" id="who1-b">Sysadmins, IT leads and security engineers who already know what a failing certificate costs. Vault handles the routine sweep so the hours go on the work that actually needs judgement.</p>',
        "ar": '<p class="who-body" id="who1-b">مسؤولو الأنظمة وقادة تقنية المعلومات ومهندسو الأمن الذين يعرفون تماماً ثمن شهادة منتهية. يتولّى Vault الجولة الروتينية لتذهب الساعات إلى العمل الذي يحتاج فعلاً إلى حُكم بشري.</p>',
        "es": '<p class="who-body" id="who1-b">Administradores de sistemas, responsables de TI e ingenieros de seguridad que ya saben lo que cuesta un certificado caducado. Vault se encarga del barrido rutinario para que las horas vayan al trabajo que de verdad exige criterio.</p>',
        "fr": '<p class="who-body" id="who1-b">Administrateurs systèmes, responsables informatiques et ingénieurs sécurité qui savent déjà ce que coûte un certificat expiré. Vault se charge du passage de routine pour que les heures aillent au travail qui demande vraiment du jugement.</p>',
        "de": '<p class="who-body" id="who1-b">Systemadministratoren, IT-Leiter und Security-Engineers, die längst wissen, was ein abgelaufenes Zertifikat kostet. Vault übernimmt den Routinedurchlauf, damit die Stunden in die Arbeit fließen, die wirklich Urteilsvermögen verlangt.</p>',
        "pt": '<p class="who-body" id="who1-b">Administradores de sistemas, responsáveis de TI e engenheiros de segurança que já sabem quanto custa um certificado expirado. O Vault trata do varrimento rotineiro para que as horas vão para o trabalho que exige mesmo critério.</p>',
        "it": '<p class="who-body" id="who1-b">Sysadmin, responsabili IT e ingegneri della sicurezza che sanno già quanto costa un certificato scaduto. Vault si occupa della scansione di routine, così le ore vanno al lavoro che richiede davvero giudizio.</p>',
        "zh": '<p class="who-body" id="who1-b">系统管理员、IT 负责人和安全工程师，他们很清楚一张过期证书的代价。Vault 负责日常巡检，把时间留给真正需要判断的工作。</p>',
        "hi": '<p class="who-body" id="who1-b">सिस्टम एडमिन, आईटी लीड और सिक्योरिटी इंजीनियर, जो जानते हैं कि एक एक्सपायर सर्टिफिकेट की कीमत क्या होती है। रोज़ की जाँच Vault संभाल लेता है, ताकि घंटे उस काम में लगें जिसमें वाकई समझ चाहिए।</p>',
    },
    "who2_h": {
        "en": '<h3 id="who2-h">People moving into security</h3>',
        "ar": '<h3 id="who2-h">من ينتقلون إلى مجال الأمن</h3>',
        "es": '<h3 id="who2-h">Quienes se están pasando a la seguridad</h3>',
        "fr": '<h3 id="who2-h">Ceux qui se tournent vers la sécurité</h3>',
        "de": '<h3 id="who2-h">Menschen, die in die Security einsteigen</h3>',
        "pt": '<h3 id="who2-h">Quem está a entrar na segurança</h3>',
        "it": '<h3 id="who2-h">Chi sta entrando nella sicurezza</h3>',
        "zh": '<h3 id="who2-h">正在转向安全领域的人</h3>',
        "hi": '<h3 id="who2-h">जो सुरक्षा के क्षेत्र में आ रहे हैं</h3>',
    },
    "who2_b": {
        "en": '<p class="who-body" id="who2-b">Every finding says what it saw, why it matters and what it did about it, in plain words. It is a working network to learn on, not a wall of acronyms.</p>',
        "ar": '<p class="who-body" id="who2-b">كل نتيجة تشرح ما رآه ولماذا يهم وما الذي فعله حياله، بكلمات واضحة. إنها شبكة حقيقية تتعلّم عليها، لا جدار من الاختصارات.</p>',
        "es": '<p class="who-body" id="who2-b">Cada hallazgo dice qué vio, por qué importa y qué hizo al respecto, en palabras claras. Es una red real sobre la que aprender, no un muro de siglas.</p>',
        "fr": '<p class="who-body" id="who2-b">Chaque constat dit ce qu\'il a vu, pourquoi cela compte et ce qu\'il a fait, en mots simples. C\'est un vrai réseau sur lequel apprendre, pas un mur d\'acronymes.</p>',
        "de": '<p class="who-body" id="who2-b">Jeder Befund sagt in klaren Worten, was gesehen wurde, warum es zählt und was dagegen getan wurde. Ein echtes Netzwerk zum Lernen, keine Wand aus Abkürzungen.</p>',
        "pt": '<p class="who-body" id="who2-b">Cada deteção diz o que viu, porque importa e o que fez a respeito, em palavras simples. É uma rede real onde aprender, não um muro de siglas.</p>',
        "it": '<p class="who-body" id="who2-b">Ogni rilevazione dice cosa ha visto, perché conta e cosa ha fatto, in parole semplici. È una rete vera su cui imparare, non un muro di sigle.</p>',
        "zh": '<p class="who-body" id="who2-b">每一条发现都用平实的话说明它看到了什么、为什么重要、又做了什么。这是一张可以上手学习的真实网络，而不是一堵缩写砌成的墙。</p>',
        "hi": '<p class="who-body" id="who2-b">हर फ़ाइंडिंग सीधी भाषा में बताती है कि उसने क्या देखा, वह क्यों मायने रखता है और उसने क्या किया। यह सीखने के लिए एक चालू नेटवर्क है, संक्षिप्ताक्षरों की दीवार नहीं।</p>',
    },
    "who3_h": {
        "en": '<h3 id="who3-h">Small teams with no security team</h3>',
        "ar": '<h3 id="who3-h">فرق صغيرة بلا فريق أمن</h3>',
        "es": '<h3 id="who3-h">Equipos pequeños sin equipo de seguridad</h3>',
        "fr": '<h3 id="who3-h">Petites équipes sans équipe sécurité</h3>',
        "de": '<h3 id="who3-h">Kleine Teams ohne Security-Team</h3>',
        "pt": '<h3 id="who3-h">Equipas pequenas sem equipa de segurança</h3>',
        "it": '<h3 id="who3-h">Piccoli team senza un team di sicurezza</h3>',
        "zh": '<h3 id="who3-h">没有安全团队的小团队</h3>',
        "hi": '<h3 id="who3-h">बिना सुरक्षा टीम वाली छोटी टीमें</h3>',
    },
    "who3_b": {
        "en": '<p class="who-body" id="who3-b">One office, one shop, one clinic. Nobody is watching the network at three in the morning, which is exactly when the certificate lapses and the disk fills.</p>',
        "ar": '<p class="who-body" id="who3-b">مكتب واحد، متجر واحد، عيادة واحدة. لا أحد يراقب الشبكة في الثالثة فجراً، وهو بالضبط الوقت الذي تنتهي فيه الشهادة ويمتلئ القرص.</p>',
        "es": '<p class="who-body" id="who3-b">Una oficina, una tienda, una clínica. Nadie vigila la red a las tres de la madrugada, que es justo cuando caduca el certificado y se llena el disco.</p>',
        "fr": '<p class="who-body" id="who3-b">Un bureau, une boutique, un cabinet. Personne ne surveille le réseau à trois heures du matin, c\'est-à-dire précisément quand le certificat expire et que le disque se remplit.</p>',
        "de": '<p class="who-body" id="who3-b">Ein Büro, ein Laden, eine Praxis. Um drei Uhr nachts schaut niemand aufs Netzwerk, und genau dann läuft das Zertifikat ab und die Platte voll.</p>',
        "pt": '<p class="who-body" id="who3-b">Um escritório, uma loja, uma clínica. Ninguém está a vigiar a rede às três da manhã, que é precisamente quando o certificado expira e o disco enche.</p>',
        "it": '<p class="who-body" id="who3-b">Un ufficio, un negozio, uno studio. Alle tre di notte nessuno guarda la rete, ed è esattamente allora che il certificato scade e il disco si riempie.</p>',
        "zh": '<p class="who-body" id="who3-b">一间办公室、一家店铺、一家诊所。凌晨三点没有人在看网络，而证书过期、磁盘写满恰恰就发生在那个时候。</p>',
        "hi": '<p class="who-body" id="who3-b">एक दफ़्तर, एक दुकान, एक क्लिनिक। रात तीन बजे नेटवर्क पर कोई नज़र नहीं रखता, और ठीक उसी समय सर्टिफिकेट खत्म होता है और डिस्क भर जाती है।</p>',
    },
    "who4_h": {
        "en": '<h3 id="who4-h">Providers looking after clients</h3>',
        "ar": '<h3 id="who4-h">مزوّدو الخدمة الذين يعتنون بالعملاء</h3>',
        "es": '<h3 id="who4-h">Proveedores que cuidan de sus clientes</h3>',
        "fr": '<h3 id="who4-h">Prestataires qui gèrent des clients</h3>',
        "de": '<h3 id="who4-h">Dienstleister, die Kunden betreuen</h3>',
        "pt": '<h3 id="who4-h">Prestadores que cuidam de clientes</h3>',
        "it": '<h3 id="who4-h">Provider che seguono i clienti</h3>',
        "zh": '<h3 id="who4-h">为客户提供托管服务的服务商</h3>',
        "hi": '<h3 id="who4-h">क्लाइंट संभालने वाले प्रोवाइडर</h3>',
    },
    "who4_b": {
        "en": '<p class="who-body" id="who4-b">Every client site on one page, each with its own sensor and its own report. What Vault fixed is already written down when the client asks.</p>',
        "ar": '<p class="who-body" id="who4-b">كل مواقع العملاء في صفحة واحدة، لكل موقع مستشعره وتقريره الخاص. ما أصلحه Vault يكون مدوَّناً سلفاً حين يسأل العميل.</p>',
        "es": '<p class="who-body" id="who4-b">Todos los sitios de clientes en una sola página, cada uno con su propio sensor y su propio informe. Lo que Vault arregló ya está por escrito cuando el cliente pregunta.</p>',
        "fr": '<p class="who-body" id="who4-b">Tous les sites clients sur une seule page, chacun avec son capteur et son rapport. Ce que Vault a corrigé est déjà consigné quand le client pose la question.</p>',
        "de": '<p class="who-body" id="who4-b">Alle Kundenstandorte auf einer Seite, jeder mit eigenem Sensor und eigenem Bericht. Was Vault behoben hat, steht schon geschrieben, wenn der Kunde fragt.</p>',
        "pt": '<p class="who-body" id="who4-b">Todos os sites de clientes numa só página, cada um com o seu sensor e o seu relatório. O que o Vault corrigiu já está registado quando o cliente pergunta.</p>',
        "it": '<p class="who-body" id="who4-b">Tutte le sedi dei clienti in una sola pagina, ognuna con il proprio sensore e il proprio report. Ciò che Vault ha risolto è già messo per iscritto quando il cliente lo chiede.</p>',
        "zh": '<p class="who-body" id="who4-b">所有客户站点集中在一个页面，各有自己的传感器和报告。客户问起时，Vault 修了什么早已白纸黑字记好。</p>',
        "hi": '<p class="who-body" id="who4-b">सभी क्लाइंट साइटें एक ही पेज पर, हर एक का अपना सेंसर और अपनी रिपोर्ट। Vault ने क्या ठीक किया, यह क्लाइंट के पूछने से पहले ही दर्ज होता है।</p>',
    },
    "integrations_eyebrow": {
        "en": '<p class="eyebrow" id="integrations-eyebrow">Integrations</p>',
        "ar": '<p class="eyebrow" id="integrations-eyebrow">التكاملات</p>',
        "es": '<p class="eyebrow" id="integrations-eyebrow">Integraciones</p>',
        "fr": '<p class="eyebrow" id="integrations-eyebrow">Intégrations</p>',
        "de": '<p class="eyebrow" id="integrations-eyebrow">Integrationen</p>',
        "pt": '<p class="eyebrow" id="integrations-eyebrow">Integrações</p>',
        "it": '<p class="eyebrow" id="integrations-eyebrow">Integrazioni</p>',
        "zh": '<p class="eyebrow" id="integrations-eyebrow">集成</p>',
        "hi": '<p class="eyebrow" id="integrations-eyebrow">इंटीग्रेशन</p>',
    },
    "integrations_h": {
        "en": '<h2 id="integrations-h">It tells you <span class="hl">where you already are.</span></h2>',
        "ar": '<h2 id="integrations-h">يخبرك <span class="hl">حيث أنت بالفعل.</span></h2>',
        "es": '<h2 id="integrations-h">Te avisa <span class="hl">donde ya estás.</span></h2>',
        "fr": '<h2 id="integrations-h">Il vous prévient <span class="hl">là où vous êtes déjà.</span></h2>',
        "de": '<h2 id="integrations-h">Es meldet sich dort, <span class="hl">wo Sie ohnehin sind.</span></h2>',
        "pt": '<h2 id="integrations-h">Avisa-o <span class="hl">onde já está.</span></h2>',
        "it": '<h2 id="integrations-h">Ti avvisa <span class="hl">dove sei già.</span></h2>',
        "zh": '<h2 id="integrations-h">它在你本来就在的地方<span class="hl">通知你。</span></h2>',
        "hi": '<h2 id="integrations-h">यह आपको वहीं बताता है <span class="hl">जहाँ आप पहले से हैं।</span></h2>',
    },
    "integrations_lede": {
        "en": '<p class="cap-lede" id="integrations-lede">Alerts go to the place you actually watch, and the important ones can be acknowledged from there without opening the dashboard.</p>',
        "ar": '<p class="cap-lede" id="integrations-lede">تذهب التنبيهات إلى المكان الذي تتابعه فعلاً، ويمكن الإقرار بالمهم منها من هناك دون فتح لوحة التحكم.</p>',
        "es": '<p class="cap-lede" id="integrations-lede">Las alertas llegan al sitio que de verdad miras, y las importantes se pueden confirmar desde ahí sin abrir el panel.</p>',
        "fr": '<p class="cap-lede" id="integrations-lede">Les alertes arrivent là où vous regardez vraiment, et les plus importantes peuvent y être acquittées sans ouvrir le tableau de bord.</p>',
        "de": '<p class="cap-lede" id="integrations-lede">Die Meldungen landen dort, wo Sie tatsächlich hinschauen, und die wichtigen lassen sich von dort bestätigen, ohne das Dashboard zu öffnen.</p>',
        "pt": '<p class="cap-lede" id="integrations-lede">Os alertas chegam ao sítio que realmente acompanha, e os importantes podem ser confirmados a partir daí sem abrir o painel.</p>',
        "it": '<p class="cap-lede" id="integrations-lede">Gli avvisi arrivano dove guardi davvero, e quelli importanti si possono confermare da lì senza aprire la dashboard.</p>',
        "zh": '<p class="cap-lede" id="integrations-lede">警报会送到你真正在看的地方，重要的那些也可以直接在那里确认，无需打开仪表板。</p>',
        "hi": '<p class="cap-lede" id="integrations-lede">अलर्ट वहीं पहुँचते हैं जहाँ आप सचमुच देखते हैं, और ज़रूरी अलर्ट वहीं से स्वीकार किए जा सकते हैं, डैशबोर्ड खोले बिना।</p>',
    },
    "int1_h": {
        "en": '<h3 id="int1-h">Slack and Microsoft Teams</h3>',
        "ar": '<h3 id="int1-h">Slack وMicrosoft Teams</h3>',
        "es": '<h3 id="int1-h">Slack y Microsoft Teams</h3>',
        "fr": '<h3 id="int1-h">Slack et Microsoft Teams</h3>',
        "de": '<h3 id="int1-h">Slack und Microsoft Teams</h3>',
        "pt": '<h3 id="int1-h">Slack e Microsoft Teams</h3>',
        "it": '<h3 id="int1-h">Slack e Microsoft Teams</h3>',
        "zh": '<h3 id="int1-h">Slack 与 Microsoft Teams</h3>',
        "hi": '<h3 id="int1-h">Slack और Microsoft Teams</h3>',
    },
    "int1_b": {
        "en": '<p class="int-body" id="int1-b">Findings arrive in the channel your team already reads, with the severity and the host on the first line.</p>',
        "ar": '<p class="int-body" id="int1-b">تصل النتائج إلى القناة التي يقرأها فريقك أصلاً، مع الخطورة واسم المضيف في السطر الأول.</p>',
        "es": '<p class="int-body" id="int1-b">Los hallazgos llegan al canal que tu equipo ya lee, con la gravedad y el host en la primera línea.</p>',
        "fr": '<p class="int-body" id="int1-b">Les constats arrivent dans le canal que votre équipe lit déjà, avec la gravité et l\'hôte sur la première ligne.</p>',
        "de": '<p class="int-body" id="int1-b">Die Befunde landen in dem Kanal, den Ihr Team ohnehin liest, mit Schweregrad und Host in der ersten Zeile.</p>',
        "pt": '<p class="int-body" id="int1-b">As deteções chegam ao canal que a sua equipa já lê, com a gravidade e o host na primeira linha.</p>',
        "it": '<p class="int-body" id="int1-b">Le rilevazioni arrivano nel canale che il tuo team già legge, con gravità e host sulla prima riga.</p>',
        "zh": '<p class="int-body" id="int1-b">发现会送进你团队本来就在看的频道，第一行就写明严重程度和主机。</p>',
        "hi": '<p class="int-body" id="int1-b">फ़ाइंडिंग उसी चैनल में आती हैं जिसे आपकी टीम पहले से पढ़ती है, पहली ही लाइन में गंभीरता और होस्ट के साथ।</p>',
    },
    "int2_h": {
        "en": '<h3 id="int2-h">PagerDuty</h3>',
        "ar": '<h3 id="int2-h">PagerDuty</h3>',
        "es": '<h3 id="int2-h">PagerDuty</h3>',
        "fr": '<h3 id="int2-h">PagerDuty</h3>',
        "de": '<h3 id="int2-h">PagerDuty</h3>',
        "pt": '<h3 id="int2-h">PagerDuty</h3>',
        "it": '<h3 id="int2-h">PagerDuty</h3>',
        "zh": '<h3 id="int2-h">PagerDuty</h3>',
        "hi": '<h3 id="int2-h">PagerDuty</h3>',
    },
    "int2_b": {
        "en": '<p class="int-body" id="int2-b">Critical findings raise a real incident, and acknowledging it there closes it in Vault too.</p>',
        "ar": '<p class="int-body" id="int2-b">النتائج الحرجة تفتح حادثة حقيقية، والإقرار بها هناك يغلقها في Vault أيضاً.</p>',
        "es": '<p class="int-body" id="int2-b">Los hallazgos críticos abren un incidente real, y confirmarlo allí también lo cierra en Vault.</p>',
        "fr": '<p class="int-body" id="int2-b">Les constats critiques ouvrent un véritable incident, et l\'acquitter là-bas le clôt aussi dans Vault.</p>',
        "de": '<p class="int-body" id="int2-b">Kritische Befunde lösen einen echten Incident aus, und wer ihn dort bestätigt, schließt ihn auch in Vault.</p>',
        "pt": '<p class="int-body" id="int2-b">As deteções críticas abrem um incidente real, e confirmá-lo aí fecha-o também no Vault.</p>',
        "it": '<p class="int-body" id="int2-b">Le rilevazioni critiche aprono un incidente vero, e confermarlo lì lo chiude anche in Vault.</p>',
        "zh": '<p class="int-body" id="int2-b">严重发现会创建真正的事件工单，在那里确认后，Vault 中也会一并关闭。</p>',
        "hi": '<p class="int-body" id="int2-b">गंभीर फ़ाइंडिंग असली इंसिडेंट बनाती हैं, और वहाँ उसे स्वीकार करते ही वह Vault में भी बंद हो जाता है।</p>',
    },
    "int3_h": {
        "en": '<h3 id="int3-h">Discord and Telegram</h3>',
        "ar": '<h3 id="int3-h">Discord وTelegram</h3>',
        "es": '<h3 id="int3-h">Discord y Telegram</h3>',
        "fr": '<h3 id="int3-h">Discord et Telegram</h3>',
        "de": '<h3 id="int3-h">Discord und Telegram</h3>',
        "pt": '<h3 id="int3-h">Discord e Telegram</h3>',
        "it": '<h3 id="int3-h">Discord e Telegram</h3>',
        "zh": '<h3 id="int3-h">Discord 与 Telegram</h3>',
        "hi": '<h3 id="int3-h">Discord और Telegram</h3>',
    },
    "int3_b": {
        "en": '<p class="int-body" id="int3-b">For the smaller setups, where the team is a group chat rather than a rota.</p>',
        "ar": '<p class="int-body" id="int3-b">للإعدادات الأصغر، حيث يكون الفريق مجموعة دردشة لا جدول مناوبات.</p>',
        "es": '<p class="int-body" id="int3-b">Para los montajes más pequeños, donde el equipo es un chat de grupo y no un cuadro de guardias.</p>',
        "fr": '<p class="int-body" id="int3-b">Pour les installations plus petites, où l\'équipe est un groupe de discussion plutôt qu\'un tableau d\'astreinte.</p>',
        "de": '<p class="int-body" id="int3-b">Für kleinere Umgebungen, in denen das Team ein Gruppenchat ist und kein Bereitschaftsplan.</p>',
        "pt": '<p class="int-body" id="int3-b">Para as instalações mais pequenas, onde a equipa é um chat de grupo e não uma escala.</p>',
        "it": '<p class="int-body" id="int3-b">Per le installazioni più piccole, dove il team è una chat di gruppo e non un turno di reperibilità.</p>',
        "zh": '<p class="int-body" id="int3-b">适合规模更小的环境，那里的团队是一个群聊，而不是一张值班表。</p>',
        "hi": '<p class="int-body" id="int3-b">छोटे सेटअप के लिए, जहाँ टीम कोई ड्यूटी रोस्टर नहीं बल्कि एक ग्रुप चैट होती है।</p>',
    },
    "int4_h": {
        "en": '<h3 id="int4-h">ntfy on your phone</h3>',
        "ar": '<h3 id="int4-h">ntfy على هاتفك</h3>',
        "es": '<h3 id="int4-h">ntfy en tu teléfono</h3>',
        "fr": '<h3 id="int4-h">ntfy sur votre téléphone</h3>',
        "de": '<h3 id="int4-h">ntfy auf dem Handy</h3>',
        "pt": '<h3 id="int4-h">ntfy no seu telemóvel</h3>',
        "it": '<h3 id="int4-h">ntfy sul telefono</h3>',
        "zh": '<h3 id="int4-h">手机上的 ntfy</h3>',
        "hi": '<h3 id="int4-h">आपके फ़ोन पर ntfy</h3>',
    },
    "int4_b": {
        "en": '<p class="int-body" id="int4-b">A push notification with the buttons on it, so a repair can be approved from a phone.</p>',
        "ar": '<p class="int-body" id="int4-b">إشعار فوري بأزراره، ليمكن الموافقة على الإصلاح من الهاتف.</p>',
        "es": '<p class="int-body" id="int4-b">Una notificación push con los botones incluidos, para aprobar una reparación desde el teléfono.</p>',
        "fr": '<p class="int-body" id="int4-b">Une notification push avec les boutons dessus, pour approuver une réparation depuis le téléphone.</p>',
        "de": '<p class="int-body" id="int4-b">Eine Push-Benachrichtigung mit den Schaltflächen darauf, sodass eine Reparatur vom Handy aus freigegeben werden kann.</p>',
        "pt": '<p class="int-body" id="int4-b">Uma notificação push com os botões incluídos, para aprovar uma reparação a partir do telemóvel.</p>',
        "it": '<p class="int-body" id="int4-b">Una notifica push con i pulsanti sopra, così una riparazione si può approvare dal telefono.</p>',
        "zh": '<p class="int-body" id="int4-b">带按钮的推送通知，修复可以直接在手机上批准。</p>',
        "hi": '<p class="int-body" id="int4-b">बटनों सहित एक पुश नोटिफ़िकेशन, ताकि मरम्मत फ़ोन से ही मंज़ूर की जा सके।</p>',
    },
    "int5_h": {
        "en": '<h3 id="int5-h">Home Assistant</h3>',
        "ar": '<h3 id="int5-h">Home Assistant</h3>',
        "es": '<h3 id="int5-h">Home Assistant</h3>',
        "fr": '<h3 id="int5-h">Home Assistant</h3>',
        "de": '<h3 id="int5-h">Home Assistant</h3>',
        "pt": '<h3 id="int5-h">Home Assistant</h3>',
        "it": '<h3 id="int5-h">Home Assistant</h3>',
        "zh": '<h3 id="int5-h">Home Assistant</h3>',
        "hi": '<h3 id="int5-h">Home Assistant</h3>',
    },
    "int5_b": {
        "en": '<p class="int-body" id="int5-b">Vault becomes another sensor in the house, and alerts can be acknowledged from your own dashboard.</p>',
        "ar": '<p class="int-body" id="int5-b">يصبح Vault مستشعراً آخر في المنزل، ويمكن الإقرار بالتنبيهات من لوحتك الخاصة.</p>',
        "es": '<p class="int-body" id="int5-b">Vault se convierte en un sensor más de la casa, y las alertas se confirman desde tu propio panel.</p>',
        "fr": '<p class="int-body" id="int5-b">Vault devient un capteur de plus dans la maison, et les alertes s\'acquittent depuis votre propre tableau de bord.</p>',
        "de": '<p class="int-body" id="int5-b">Vault wird zu einem weiteren Sensor im Haus, und Meldungen lassen sich über Ihr eigenes Dashboard bestätigen.</p>',
        "pt": '<p class="int-body" id="int5-b">O Vault torna-se mais um sensor da casa, e os alertas podem ser confirmados a partir do seu próprio painel.</p>',
        "it": '<p class="int-body" id="int5-b">Vault diventa un altro sensore della casa, e gli avvisi si possono confermare dalla tua dashboard.</p>',
        "zh": '<p class="int-body" id="int5-b">Vault 成为家里的又一个传感器，警报可以在你自己的面板上确认。</p>',
        "hi": '<p class="int-body" id="int5-b">Vault घर का एक और सेंसर बन जाता है, और अलर्ट आपके अपने डैशबोर्ड से स्वीकार किए जा सकते हैं।</p>',
    },
    "int6_h": {
        "en": '<h3 id="int6-h">Webhooks and the API</h3>',
        "ar": '<h3 id="int6-h">الويب هوك وواجهة البرمجة (API)</h3>',
        "es": '<h3 id="int6-h">Webhooks y la API</h3>',
        "fr": '<h3 id="int6-h">Webhooks et l\'API</h3>',
        "de": '<h3 id="int6-h">Webhooks und die API</h3>',
        "pt": '<h3 id="int6-h">Webhooks e a API</h3>',
        "it": '<h3 id="int6-h">Webhook e API</h3>',
        "zh": '<h3 id="int6-h">Webhook 与 API</h3>',
        "hi": '<h3 id="int6-h">वेबहुक और API</h3>',
    },
    "int6_b": {
        "en": '<p class="int-body" id="int6-b">Every finding as JSON, to your own endpoint or pulled from the public API with a token you issue.</p>',
        "ar": '<p class="int-body" id="int6-b">كل نتيجة بصيغة JSON، إلى نقطة النهاية الخاصة بك أو تُسحب من الواجهة العامة برمز تصدره أنت.</p>',
        "es": '<p class="int-body" id="int6-b">Cada hallazgo en JSON, a tu propio endpoint o descargado de la API pública con un token que emites tú.</p>',
        "fr": '<p class="int-body" id="int6-b">Chaque constat en JSON, vers votre propre point de terminaison ou récupéré depuis l\'API publique avec un jeton que vous émettez.</p>',
        "de": '<p class="int-body" id="int6-b">Jeder Befund als JSON, an Ihren eigenen Endpunkt oder über die öffentliche API abgerufen, mit einem Token, das Sie ausstellen.</p>',
        "pt": '<p class="int-body" id="int6-b">Cada deteção em JSON, para o seu próprio endpoint ou obtida da API pública com um token emitido por si.</p>',
        "it": '<p class="int-body" id="int6-b">Ogni rilevazione in JSON, verso il tuo endpoint o prelevata dall\'API pubblica con un token che emetti tu.</p>',
        "zh": '<p class="int-body" id="int6-b">每条发现都以 JSON 形式发往你自己的端点，或用你签发的令牌从公开 API 拉取。</p>',
        "hi": '<p class="int-body" id="int6-b">हर फ़ाइंडिंग JSON में, आपके अपने एंडपॉइंट पर या आपके जारी किए टोकन से पब्लिक API से ली गई।</p>',
    },
})

# ---------------------------------------------------------------------------
# ONE_OFF: multi-line elements that appear exactly once, matched by a regex
# anchored on their unique class/id so the original's internal line-wrapping
# never has to be typed out again here -- only the translated text does.
# Each tuple is (pattern, {locale: translated_inner_html}); the pattern must
# have exactly one capture group, the element's inner HTML.
# ---------------------------------------------------------------------------

ONE_OFF = [
    (
        r'(<p class="sub">)(.*?)(</p>)',
        {
            "ar": "يراقب الأجهزة والمواقع وحركة البيانات التي أنت مسؤول عنها"
                  " &mdash; وبالنسبة للمشاكل الروتينية، لا يخبرك فقط، بل"
                  " يصلحها. صُنع لمن يتلقّى المكالمة عندما تتعطّل الشبكة:"
                  " منزل واحد، مكتب واحد، أو مئات مواقع العملاء.",
            "es": "Vigila las máquinas, los sitios web y el tráfico de los que"
                  " eres responsable &mdash; y, ante los problemas rutinarios,"
                  " no solo te avisa: los soluciona. Pensado para la persona"
                  " que recibe la llamada cuando la red se cae: una casa, una"
                  " oficina o cien sitios de clientes.",
            "fr": "Il surveille les machines, les sites web et le trafic dont"
                  " vous êtes responsable &mdash; et pour les problèmes"
                  " courants, il ne se contente pas de vous prévenir, il les"
                  " corrige. Conçu pour la personne qui reçoit l'appel quand"
                  " le réseau tombe en panne : une maison, un bureau, ou une"
                  " centaine de sites clients.",
            "de": "Es überwacht die Maschinen, Websites und den Datenverkehr,"
                  " für die Sie zuständig sind &mdash; und bei Routineproblemen"
                  " sagt es Ihnen nicht nur Bescheid, es behebt sie. Gemacht für"
                  " die Person, die den Anruf bekommt, wenn das Netzwerk"
                  " ausfällt: ein Haus, ein Büro oder hundert Kundenstandorte.",
            "pt": "Vigia as máquinas, os sites e o tráfego pelos quais você é"
                  " responsável &mdash; e, para os problemas rotineiros, não se"
                  " limita a avisar: resolve-os. Feito para quem recebe a"
                  " ligação quando a rede cai: uma casa, um escritório, ou uma"
                  " centena de sites de clientes.",
            "it": "Sorveglia le macchine, i siti web e il traffico di cui sei"
                  " responsabile &mdash; e per i problemi di routine non si"
                  " limita ad avvisarti: li risolve. Pensato per chi riceve la"
                  " chiamata quando la rete si guasta: una casa, un ufficio, o"
                  " un centinaio di sedi clienti.",
            "zh": "它监控着你负责的机器、网站和流量 &mdash; 遇到日常问题时，它不只是"
                  "告诉你，还会直接修复。为那个网络出故障时会接到电话的人打造："
                  "一个家、一间办公室，或上百个客户站点。",
            "hi": "यह उन मशीनों, वेबसाइट और ट्रैफ़िक पर नज़र रखता है जिनके लिए आप"
                  " जिम्मेदार हैं &mdash; और सामान्य समस्याओं के लिए यह सिर्फ़ बताता"
                  " नहीं, बल्कि उन्हें ठीक भी करता है। यह उस व्यक्ति के लिए बनाया गया"
                  " है जिसे नेटवर्क खराब होने पर कॉल आता है: एक घर, एक ऑफ़िस, या सैकड़ों"
                  " क्लाइंट साइटें।",
        },
    ),
    (
        r'(<h2 id="live-h" class="panel-title">\s*<span class="live-dot" aria-hidden="true"></span>)(.*?)(\s*</h2>)',
        {
            "ar": "مباشر من الشبكة",
            "es": "En vivo desde la red",
            "fr": "En direct depuis le réseau",
            "de": "Live aus dem Netzwerk",
            "pt": "Em direto a partir da rede",
            "it": "Dal vivo dalla rete",
            "zh": "来自网络的实时数据",
            "hi": "नेटवर्क से लाइव",
        },
    ),
    (
        r'(<p class="feed-fallback" id="feed-fallback">)(.*?)(</p>)',
        {
            "ar": "يعرض هذا الجزء عادةً التنبيهات التي رصدها Vault على شبكتنا"
                  " التي نراقبها في الأيام الأخيرة &mdash; أجهزة تنضم وأجهزة تصمت،"
                  " منافذ تُفتح، عمليات بحث تُحظر، إصلاحات تتم. يحتاج إلى"
                  " جافاسكريبت، ويجب أن يكون الخادم المركزي متاحًا. بقية"
                  " الصفحة لا تحتاج إلى ذلك.",
            "es": "Este panel normalmente muestra en streaming los avisos que"
                  " Vault generó en una red vigilada en"
                  " los últimos días &mdash; dispositivos que llegan y se"
                  " quedan en silencio, puertos que se abren, búsquedas"
                  " bloqueadas, reparaciones hechas. Necesita JavaScript, y el"
                  " hub debe ser accesible. El resto de la página no.",
            "fr": "Ce panneau diffuse normalement les notifications que"
                  " Vault a levées sur un réseau surveillé"
                  " ces derniers jours &mdash; appareils qui arrivent et se"
                  " taisent, ports qui s'ouvrent, requêtes bloquées, réparations"
                  " effectuées. Il a besoin de JavaScript, et le hub doit être"
                  " accessible. Le reste de la page n'en a pas besoin.",
            "de": "Dieses Panel zeigt normalerweise laufend die Meldungen, die"
                  " Vault in den letzten Tagen auf einem überwachten"
                  " Netzwerk ausgelöst hat &mdash; Geräte, die auftauchen"
                  " und verstummen, Ports, die sich öffnen, blockierte Abfragen,"
                  " durchgeführte Reparaturen. Es braucht JavaScript, und der"
                  " Hub muss erreichbar sein. Der Rest der Seite nicht.",
            "pt": "Este painel normalmente transmite os avisos que o Vault"
                  " levantou numa rede vigiada nos últimos"
                  " dias &mdash; dispositivos a chegar e a ficar em silêncio,"
                  " portas a abrir, pesquisas bloqueadas, reparos feitos."
                  " Precisa de JavaScript, e o hub tem de estar acessível. O"
                  " resto da página não precisa.",
            "it": "Questo pannello normalmente mostra in streaming gli avvisi"
                  " che Vault ha generato su una rete sorvegliata negli"
                  " ultimi giorni &mdash; dispositivi che arrivano e"
                  " ammutoliscono, porte che si aprono, ricerche bloccate,"
                  " riparazioni effettuate. Serve JavaScript, e il hub deve"
                  " essere raggiungibile. Il resto della pagina no.",
            "zh": "该面板通常会实时显示 Vault 在一个受监控的网络上最近几天发出的"
                  "通知 &mdash; 设备的加入和沉寂、端口的开放、被拦截的查询、已完成的"
                  "修复。它需要 JavaScript，并且中枢必须可以访问。页面的其余部分不需要。",
            "hi": "यह पैनल आम तौर पर उन नोटिस को लाइव दिखाता है जो Vault ने"
                  " एक निगरानी वाले नेटवर्क पर पिछले कुछ दिनों में उठाए हैं"
                  " &mdash; डिवाइस का आना और शांत हो जाना, पोर्ट का खुलना, रोकी"
                  " गई लुकअप, की गई मरम्मत। इसके लिए JavaScript चाहिए, और हब तक"
                  " पहुँच होना ज़रूरी है। पेज का बाकी हिस्सा इस पर निर्भर नहीं है।",
        },
    ),
    (
        r'(<p class="panel-foot">)(.*?)(</p>)',
        {
            "ar": "الأحدث أولًا &middot; يتم التحديث كل بضع ثوانٍ",
            "es": "Las más recientes primero &middot; se actualiza cada pocos"
                  " segundos",
            "fr": "Les plus récentes en premier &middot; actualisé toutes les"
                  " quelques secondes",
            "de": "Neueste zuerst &middot; alle paar Sekunden aktualisiert",
            "pt": "As mais recentes primeiro &middot; atualizado a cada poucos"
                  " segundos",
            "it": "Le più recenti prima &middot; aggiornato ogni pochi secondi",
            "zh": "最新的在前 &middot; 每隔几秒刷新一次",
            "hi": "सबसे नई सबसे पहले &middot; हर कुछ सेकंड में रीफ़्रेश होता है",
        },
    ),
    (
        r'(<p class="quotes-note">)(.*?)(</p>)',
        {
            "ar": "توضيحية &mdash; الجمل التي يبدأ بها بلاغ عادةً، وليست"
                  " اقتباسات من عملاء معروفين بالاسم.",
            "es": "Ilustrativo &mdash; las frases con las que suele empezar"
                  " un aviso, no citas de clientes con nombre.",
            "fr": "Illustratif &mdash; les phrases par lesquelles commence"
                  " généralement un signalement, pas des citations de clients"
                  " nommés.",
            "de": "Illustrativ &mdash; die Sätze, mit denen eine Meldung"
                  " meist beginnt, keine Zitate namentlich genannter Kunden.",
            "pt": "Ilustrativo &mdash; as frases com que normalmente começa"
                  " um chamado, não citações de clientes identificados.",
            "it": "Illustrativo &mdash; le frasi con cui di solito inizia una"
                  " segnalazione, non citazioni di clienti con nome.",
            "zh": "示例性内容 &mdash; 是报修电话通常开头的那些话，并非具名客户的原话。",
            "hi": "उदाहरणस्वरूप &mdash; ये वे वाक्य हैं जिनसे आम तौर पर शिकायत"
                  " शुरू होती है, किसी नामित ग्राहक के उद्धरण नहीं हैं।",
        },
    ),
]

# ---------------------------------------------------------------------------
# SEQUENTIAL: repeated element shapes where only document order tells one
# instance from another (there is no unique class/id per-instance). Each
# group is (pattern, [translations-dict, ...]) with the translations listed
# in the exact order the pattern's matches occur top-to-bottom in the file.
# build()'s run_sequential() helper consumes one match per list entry, so
# the Nth dict lands on the Nth occurrence -- get the order in this file wrong and
# it silently mistranslates a different paragraph, so it is re-derived from
# a fresh `grep -n` of site/index.html any time a paragraph is added, moved,
# or removed, rather than trusted from memory.
# ---------------------------------------------------------------------------

LEDE_PATTERN = r'(<p class="lede">)(.*?)(</p>)'
LEDE = [
    {  # 1. problem section, before the quote list
        "ar": "لا شيء في الشبكة يُعلن عن نفسه. القرص يمتلئ بصمت، الشهادة"
              " تنتهي بصمت، الجهاز الجديد ينضم بصمت. غير الصامت هو المكالمة"
              " الهاتفية التي تأتي بعد ذلك.",
        "es": "Nada en una red anuncia lo que pasa. El disco se llena en"
              " silencio, el certificado caduca en silencio, el dispositivo"
              " nuevo se conecta en silencio. Lo que no es silencioso es la"
              " llamada telefónica que viene después.",
        "fr": "Rien sur un réseau ne s'annonce. Le disque se remplit en"
              " silence, le certificat expire en silence, le nouvel appareil"
              " se connecte en silence. Ce qui n'est pas silencieux, c'est"
              " l'appel téléphonique qui suit.",
        "de": "Nichts in einem Netzwerk meldet sich selbst. Die Festplatte"
              " läuft still voll, das Zertifikat läuft still ab, das neue"
              " Gerät verbindet sich still. Nicht still ist der Anruf, der"
              " danach kommt.",
        "pt": "Nada numa rede se anuncia. O disco enche-se em silêncio, o"
              " certificado expira em silêncio, o novo dispositivo liga-se"
              " em silêncio. O que não é silencioso é a chamada telefónica"
              " que vem depois.",
        "it": "Niente su una rete annuncia se stesso. Il disco si riempie in"
              " silenzio, il certificato scade in silenzio, il nuovo"
              " dispositivo si collega in silenzio. Quello che non è"
              " silenzioso è la telefonata che arriva dopo.",
        "zh": "网络上的任何变化都不会主动通知你。磁盘悄悄写满、证书悄悄过期、"
              "新设备悄悄接入。唯一不悄悄的，是之后打来的那个电话。",
        "hi": "नेटवर्क पर कुछ भी अपने बारे में खुद नहीं बताता। डिस्क चुपचाप"
              " भर जाती है, सर्टिफ़िकेट चुपचाप खत्म हो जाता है, नया डिवाइस"
              " चुपचाप जुड़ जाता है। जो चुपचाप नहीं होता, वह है बाद में आने"
              " वाला फ़ोन कॉल।",
    },
    {  # 2. problem section, after the quote list
        "ar": "لا يمكن لأحد مراقبة شبكة يدويًا في الساعة الثالثة فجرًا. أما"
              " شيء يجلس عليها ويراقب، فيمكنه ذلك.",
        "es": "Nadie puede vigilar una red a mano a las tres de la"
              " madrugada. Algo que esté ahí, mirando, sí puede.",
        "fr": "Personne ne peut surveiller un réseau à la main à trois"
              " heures du matin. Quelque chose posté là, à observer, le"
              " peut.",
        "de": "Niemand kann ein Netzwerk um drei Uhr morgens von Hand"
              " überwachen. Etwas, das dort sitzt und zusieht, kann es.",
        "pt": "Ninguém consegue vigiar uma rede manualmente às três da"
              " manhã. Algo que esteja lá, a observar, consegue.",
        "it": "Nessuno può sorvegliare una rete a mano alle tre del"
              " mattino. Qualcosa che sta lì, a guardare, può farlo.",
        "zh": "凌晨三点，没有人能靠人力盯着网络。但一个一直在那里盯着的东西可以。",
        "hi": "रात के तीन बजे कोई भी हाथ से नेटवर्क पर नज़र नहीं रख सकता।"
              " लेकिन कुछ ऐसा जो वहाँ बैठा देख रहा हो, रख सकता है।",
    },
    {  # 3. watches section
        "ar": "مستشعر واحد على الشبكة، وصفحة واحدة تحتوي كل شيء. لا حاجة"
              " للبحث في ستة لوحات تحكم مختلفة للإجابة عن سؤال واحد.",
        "es": "Un sensor en la red, una página con todo. Sin tener que"
              " buscar en seis paneles de administración distintos para"
              " responder una sola pregunta.",
        "fr": "Un capteur sur le réseau, une page avec tout dessus. Plus"
              " besoin de fouiller dans six panneaux d'administration"
              " différents pour répondre à une seule question.",
        "de": "Ein Sensor im Netzwerk, eine Seite mit allem darauf. Kein"
              " Durchsuchen von sechs verschiedenen Admin-Oberflächen mehr,"
              " um eine einzige Frage zu beantworten.",
        "pt": "Um sensor na rede, uma página com tudo. Sem ter de procurar"
              " em seis painéis de administração diferentes para responder"
              " a uma única pergunta.",
        "it": "Un sensore sulla rete, una pagina con tutto. Niente più"
              " ricerche in sei pannelli di amministrazione diversi per"
              " rispondere a una sola domanda.",
        "zh": "网络上一个传感器，一个页面看到所有信息。不用再翻六个不同的管理"
              "面板才能回答一个问题。",
        "hi": "नेटवर्क पर एक सेंसर, और एक पेज पर सब कुछ। एक सवाल का जवाब"
              " ढूँढने के लिए छह अलग-अलग एडमिन पैनल में भटकने की ज़रूरत नहीं।",
    },
    {  # 4. how section, single line in the source
        "ar": "ثلاث خطوات، وستكون تنظر إلى شبكتك الخاصة.",
        "es": "Tres pasos, y estarás viendo tu propia red.",
        "fr": "Trois étapes, et vous voilà face à votre propre réseau.",
        "de": "Drei Schritte, und Sie blicken auf Ihr eigenes Netzwerk.",
        "pt": "Três passos, e estará a ver a sua própria rede.",
        "it": "Tre passaggi, e starai guardando la tua rete.",
        "zh": "只需三步，你就能看到自己的网络。",
        "hi": "तीन चरण, और आप अपने ही नेटवर्क को देख रहे होंगे।",
    },
    {  # 5. fixes section
        "ar": "لا تُفيد التنبيهات إلا إذا تصرّف أحدهم بناءً عليها، وفي"
              " الساعة الثانية فجرًا لا يفعل أحد ذلك. هذا هو الجزء من Vault"
              " الذي ليس لوحة تحكم.",
        "es": "Una alerta solo sirve si alguien actúa sobre ella, y a las 2"
              " de la madrugada nadie lo hace. Esta es la parte de Vault"
              " que no es un panel.",
        "fr": "Une alerte n'est utile que si quelqu'un agit en conséquence,"
              " et à 2 h du matin, personne ne le fait. C'est la partie de"
              " Vault qui n'est pas un tableau de bord.",
        "de": "Eine Warnung nützt nur, wenn jemand darauf reagiert, und um"
              " 2 Uhr nachts tut das niemand. Das ist der Teil von Vault,"
              " der kein Dashboard ist.",
        "pt": "Um alerta só é útil se alguém agir sobre ele, e às 2 da"
              " manhã ninguém age. Esta é a parte do Vault que não é um"
              " painel.",
        "it": "Un avviso serve solo se qualcuno agisce di conseguenza, e"
              " alle 2 di notte nessuno lo fa. Questa è la parte di Vault"
              " che non è una dashboard.",
        "zh": "警报只有在有人采取行动时才有用，而凌晨两点没人会这么做。这正是"
              "Vault 不只是一个仪表盘的地方。",
        "hi": "अलर्ट तभी काम का है जब कोई उस पर कार्रवाई करे, और रात के 2 बजे"
              " कोई नहीं करता। Vault का यही वह हिस्सा है जो सिर्फ़ डैशबोर्ड"
              " नहीं है।",
    },
    {  # 6. access / lead-capture section (text: brief's Step 6 replacement --
       # "Tell us what you are looking after and we will set you up ...")
        "ar": "أخبرنا بما تديره وسنجهّز لك كل شيء &mdash; لا يوجد نموذج بطاقة"
              " ولا شيء تدفعه، لأن كل عملية نشر لا تزال تتم يدويًا في هذه"
              " المرحلة.",
        "es": "Cuéntanos qué red administras y te configuraremos &mdash; no"
              " hay formulario de tarjeta ni nada que pagar, porque cada"
              " despliegue todavía se hace a mano en esta etapa.",
        "fr": "Dites-nous de quoi vous vous occupez et nous vous"
              " installerons &mdash; il n'y a pas de formulaire de carte ni"
              " rien à payer, car chaque déploiement se fait encore à la"
              " main à ce stade.",
        "de": "Sagen Sie uns, was Sie betreuen, und wir richten Sie ein"
              " &mdash; es gibt kein Kartenformular und nichts zu bezahlen,"
              " denn jede Einrichtung erfolgt in dieser Phase noch von Hand.",
        "pt": "Diga-nos o que você administra e nós o configuramos"
              " &mdash; não há formulário de cartão nem nada a pagar, porque"
              " cada implementação ainda é feita manualmente nesta fase.",
        "it": "Dicci di cosa ti occupi e ti configureremo &mdash; non c'è"
              " nessun modulo per la carta e nulla da pagare, perché ogni"
              " distribuzione viene ancora fatta a mano in questa fase.",
        "zh": "告诉我们你在管理什么，我们会为你完成配置 &mdash; 没有信用卡表单，"
              "也没有任何费用，因为目前这个阶段每一次部署仍是人工完成的。",
        "hi": "हमें बताएं कि आप किसकी देखरेख कर रहे हैं, हम आपको सेट अप कर"
              " देंगे &mdash; न कोई कार्ड फ़ॉर्म है, न कुछ चुकाना है, क्योंकि"
              " इस चरण में हर डिप्लॉयमेंट अभी भी हाथ से किया जाता है।",
    },
]

# `(?: id="[^"]*")?` makes the pattern match both `<p class="cta-note">` and
# the one instance that also carries `id="pricing-no-checkout"`.
CTA_NOTE_PATTERN = r'(<p(?: id="[^"]*")? class="cta-note">)(.*?)(</p>)'
CTA_NOTE = [
    {  # 1. hero section
        "ar": "لا بطاقة ولا دفع &mdash; الفوترة غير موجودة في المنتج بعد،"
              " فالوصول يُمنح يدويًا، عملية نشر تلو الأخرى.",
        "es": "Sin tarjeta y sin pago &mdash; la facturación aún no existe"
              " en el producto, así que el acceso se concede a mano, un"
              " despliegue a la vez.",
        "fr": "Ni carte ni paiement &mdash; la facturation n'existe pas"
              " encore dans le produit, donc l'accès est accordé à la"
              " main, un déploiement à la fois.",
        "de": "Keine Karte, keine Zahlung &mdash; Abrechnung gibt es im"
              " Produkt noch nicht, daher wird der Zugang von Hand gewährt,"
              " ein Deployment nach dem anderen.",
        "pt": "Sem cartão e sem cobrança &mdash; a faturação ainda não"
              " existe no produto, por isso o acesso é concedido"
              " manualmente, uma implementação de cada vez.",
        "it": "Nessuna carta e nessun pagamento &mdash; la fatturazione non"
              " esiste ancora nel prodotto, quindi l'accesso viene concesso"
              " a mano, una distribuzione alla volta.",
        "zh": "无需信用卡，也无需结账 &mdash; 产品里还没有计费功能，因此访问权限"
              "目前是人工逐一开通的。",
        "hi": "न कोई कार्ड, न कोई चेकआउट &mdash; प्रोडक्ट में अभी बिलिंग नहीं"
              " है, इसलिए एक्सेस हाथ से दिया जाता है, एक बार में एक डिप्लॉयमेंट।",
    },
    {  # 2. how-it-works section, under the install command
        "ar": "يأتي رمز التسجيل من لوحة التحكم الخاصة بك وهو صالح لمستشعر"
              " واحد. أضف شبكة ثانية لاحقًا وستحصل على رمزها الخاص.",
        "es": "El código de inscripción viene de tu propio panel y es"
              " válido para un sensor. Añade una segunda red más adelante"
              " y tendrá el suyo.",
        "fr": "Le code d'inscription vient de votre propre tableau de bord"
              " et n'est valable que pour un capteur. Ajoutez un second"
              " réseau plus tard et il aura le sien.",
        "de": "Der Anmeldecode stammt aus Ihrem eigenen Dashboard und gilt"
              " für einen Sensor. Fügen Sie später ein zweites Netzwerk"
              " hinzu, und es erhält seinen eigenen.",
        "pt": "O código de registo vem do seu próprio painel e é válido"
              " para um sensor. Adicione uma segunda rede mais tarde e ela"
              " terá o seu próprio código.",
        "it": "Il codice di registrazione arriva dalla tua dashboard ed è"
              " valido per un sensore. Aggiungi una seconda rete più avanti"
              " e avrà il suo.",
        "zh": "注册码来自你自己的仪表盘，且只对一个传感器有效。之后再添加第二个"
              "网络时，它会有自己的注册码。",
        "hi": "एनरोलमेंट कोड आपके अपने डैशबोर्ड से आता है और एक सेंसर के लिए"
              " वैध होता है। बाद में दूसरा नेटवर्क जोड़ें और उसे अपना कोड मिलेगा।",
    },
]

PLAIN_P_PATTERN = r'(<p>)(.*?)(</p>)'
PLAIN_P = [
    {  # 1. watches card 1 -- who is on the network
        "ar": "كل جهاز ينضم، وكل جهاز يصمت، والأجهزة التي لم ترها من قبل."
              " سمِّ الأجهزة التي تعرفها مرة واحدة ويتوقف Vault عن سؤالك"
              " عنها.",
        "es": "Cada dispositivo que se une, cada dispositivo que se queda"
              " en silencio, y los que nunca has visto antes. Nombra los"
              " que reconozcas una vez y Vault deja de preguntarte por"
              " ellos.",
        "fr": "Chaque appareil qui se connecte, chaque appareil qui se"
              " tait, et ceux que vous n'avez jamais vus. Nommez une fois"
              " ceux que vous reconnaissez et Vault arrête de vous"
              " interroger à leur sujet.",
        "de": "Jedes Gerät, das sich verbindet, jedes Gerät, das"
              " verstummt, und die, die Sie noch nie gesehen haben."
              " Benennen Sie die, die Sie erkennen, einmal, und Vault"
              " fragt nicht mehr danach.",
        "pt": "Cada dispositivo que se junta, cada dispositivo que fica em"
              " silêncio, e os que nunca viu antes. Dê nome aos que"
              " reconhece uma vez e o Vault para de perguntar sobre"
              " eles.",
        "it": "Ogni dispositivo che si collega, ogni dispositivo che"
              " ammutolisce, e quelli che non hai mai visto prima."
              " Assegna un nome a quelli che riconosci una volta e Vault"
              " smette di chiedertelo.",
        "zh": "每一台加入的设备，每一台沉寂下来的设备，以及那些你从未见过的设备。"
              "把你认识的设备命名一次，Vault 就不会再为它们打扰你。",
        "hi": "हर जुड़ने वाला डिवाइस, हर शांत हो जाने वाला डिवाइस, और वे"
              " जिन्हें आपने पहले कभी नहीं देखा। जिन्हें आप पहचानते हैं उनका"
              " नाम एक बार रख दें, फिर Vault उनके बारे में पूछना बंद कर"
              " देता है।",
    },
    {  # 2. watches card 2 -- your servers
        "ar": "القرص والذاكرة والحمل، والتحديثات التي تنتظر التثبيت &mdash;"
              " بما في ذلك تلك التي تحتاج إعادة تشغيل لتُحسب. تعرف أن قرصًا"
              " يمتلئ بينما لا يزال مجرد إزعاج.",
        "es": "Disco, memoria, carga, y las actualizaciones esperando"
              " instalarse &mdash; incluidas las que necesitan un reinicio"
              " para contar. Te enteras de que un disco se está llenando"
              " mientras todavía es solo una molestia.",
        "fr": "Disque, mémoire, charge, et les mises à jour en attente"
              " d'installation &mdash; y compris celles qui ont besoin d'un"
              " redémarrage pour compter. Vous découvrez qu'un disque se"
              " remplit alors que ce n'est encore qu'une nuisance.",
        "de": "Festplatte, Speicher, Last, und die Updates, die auf die"
              " Installation warten &mdash; einschließlich der, die einen"
              " Neustart brauchen, um zu zählen. Sie erfahren, dass eine"
              " Festplatte sich füllt, während es noch ein Ärgernis ist.",
        "pt": "Disco, memória, carga, e as atualizações à espera de"
              " instalação &mdash; incluindo as que precisam de um reinício"
              " para contar. Você descobre que um disco está a ficar cheio"
              " enquanto ainda é só um incómodo.",
        "it": "Disco, memoria, carico, e gli aggiornamenti in attesa di"
              " installazione &mdash; incluse quelle che richiedono un"
              " riavvio per essere effettive. Scopri che un disco si sta"
              " riempiendo mentre è ancora solo un fastidio.",
        "zh": "磁盘、内存、负载，以及等待安装的更新 &mdash; 包括那些需要重启才能"
              "生效的更新。磁盘还只是有点烦人的时候，你就已经知道它在被填满。",
        "hi": "डिस्क, मेमोरी, लोड, और इंस्टॉल होने का इंतज़ार कर रहे अपडेट"
              " &mdash; उनमें वे भी शामिल हैं जिन्हें असर दिखाने के लिए रीबूट"
              " चाहिए। आपको पता चल जाता है कि डिस्क भर रही है, जब वह अभी"
              " सिर्फ़ एक झुंझलाहट है।",
    },
    {  # 3. watches card 3 -- websites and certificates
        "ar": "هل يستجيب كل موقع، وبأي سرعة، وكم يومًا بقي على شهادته."
              " الشهادة التي تقترب من انتهائها تحذير قبل أسابيع، وليست"
              " انقطاعًا يوم الأحد.",
        "es": "Si cada sitio responde, con qué rapidez, y cuántos días le"
              " quedan a su certificado. Un certificado que va a caducar"
              " es un aviso con semanas de antelación, no una caída un"
              " domingo.",
        "fr": "Si chaque site répond, à quelle vitesse, et combien de"
              " jours il reste avant l'expiration de son certificat. Un"
              " certificat qui expire est un avertissement des semaines à"
              " l'avance, pas une panne un dimanche.",
        "de": "Ob jede Website antwortet, wie schnell, und wie viele Tage"
              " ihr Zertifikat noch hat. Ein ablaufendes Zertifikat ist eine"
              " Warnung Wochen im Voraus, kein Ausfall an einem Sonntag.",
        "pt": "Se cada site responde, com que rapidez, e quantos dias"
              " restam para o seu certificado. Um certificado a expirar é"
              " um aviso com semanas de antecedência, não uma falha num"
              " domingo.",
        "it": "Se ogni sito risponde, con quale rapidità, e quanti giorni"
              " restano al suo certificato. Un certificato in scadenza è un"
              " avviso con settimane di anticipo, non un guasto di domenica.",
        "zh": "每个网站是否响应、响应速度如何，以及它的证书还剩多少天。一张即将"
              "过期的证书是提前几周的预警，而不是某个星期天的宕机事故。",
        "hi": "हर साइट जवाब देती है या नहीं, कितनी तेज़ी से, और उसके"
              " सर्टिफ़िकेट में कितने दिन बचे हैं। खत्म होने वाला सर्टिफ़िकेट"
              " हफ़्तों पहले की चेतावनी है, किसी रविवार का आउटेज नहीं।",
    },
    {  # 4. watches card 4 -- where the traffic goes
        "ar": "الأسماء التي تبحث عنها أجهزتك، وأيها تم حظره، وأي جهاز طلبها."
              " مفيد عندما يتحدث تلفزيون ذكي إلى مكان غريب، ومفيد عندما"
              " ينكسر شيء وتحتاج لمعرفة ما الذي حاول الوصول إليه.",
        "es": "Los nombres que buscan tus dispositivos, cuáles se"
              " bloquearon, y qué dispositivo los pidió. Útil cuando un"
              " televisor inteligente charla con algún sitio raro, y útil"
              " cuando algo se rompe y necesitas saber a qué intentó"
              " llegar.",
        "fr": "Les noms recherchés par vos appareils, ceux qui ont été"
              " bloqués, et quel appareil les a demandés. Utile quand une"
              " télé connectée discute avec un endroit bizarre, et utile"
              " quand quelque chose casse et que vous devez savoir ce"
              " qu'il cherchait à atteindre.",
        "de": "Die Namen, die Ihre Geräte nachschlagen, welche blockiert"
              " wurden, und welches Gerät gefragt hat. Nützlich, wenn ein"
              " Smart-TV mit einem seltsamen Ort spricht, und nützlich,"
              " wenn etwas kaputt ist und Sie wissen müssen, was es"
              " erreichen wollte.",
        "pt": "Os nomes que os seus dispositivos procuram, quais foram"
              " bloqueados, e qual dispositivo pediu. Útil quando uma"
              " smart TV está a conversar com algum lugar estranho, e útil"
              " quando algo se avaria e precisa de saber o que estava a"
              " tentar alcançar.",
        "it": "I nomi che i tuoi dispositivi cercano, quali sono stati"
              " bloccati, e quale dispositivo li ha richiesti. Utile"
              " quando una smart TV chiacchiera con un posto strano, e"
              " utile quando qualcosa si rompe e devi sapere cosa stava"
              " cercando di raggiungere.",
        "zh": "你的设备查询过哪些名称，哪些被拦截了，以及是哪台设备发起的查询。"
              "当一台智能电视莫名其妙地连到某个陌生地址时很有用，当出现故障、"
              "你需要知道它想连到哪里时也很有用。",
        "hi": "आपके डिवाइस जो नाम खोजते हैं, उनमें से कौन-से ब्लॉक हुए, और"
              " किस डिवाइस ने पूछा। यह तब काम आता है जब कोई स्मार्ट टीवी किसी"
              " अजीब जगह से बात कर रहा हो, और तब भी जब कुछ खराब हो जाए और"
              " आपको जानना हो कि वह कहाँ पहुँचने की कोशिश कर रहा था।",
    },
    {  # 5. watches card 5 -- exposed ports / who is knocking
        "ar": "المنافذ المكشوفة فعليًا من الخارج، والعناوين التي تحاول"
              " الوصول إليها &mdash; من أين هي وماذا كانت تبحث عنه.",
        "es": "Los puertos realmente accesibles desde fuera, y las"
              " direcciones que los intentan &mdash; de dónde son y qué"
              " buscaban.",
        "fr": "Les ports réellement accessibles depuis l'extérieur, et les"
              " adresses qui les tentent &mdash; d'où elles viennent et ce"
              " qu'elles cherchaient.",
        "de": "Die von außen tatsächlich erreichbaren Ports, und die"
              " Adressen, die es versuchen &mdash; woher sie kommen und"
              " was sie gesucht haben.",
        "pt": "As portas realmente acessíveis do exterior, e os endereços"
              " que as tentam &mdash; de onde são e o que procuravam.",
        "it": "Le porte effettivamente raggiungibili dall'esterno, e gli"
              " indirizzi che le tentano &mdash; da dove provengono e cosa"
              " cercavano.",
        "zh": "真正能从外部访问到的端口，以及尝试连接它们的地址 &mdash; 它们来自"
              "哪里、又想找什么。",
        "hi": "बाहर से वास्तव में पहुँचे जा सकने वाले पोर्ट, और उन्हें आज़माने"
              " वाले पते &mdash; वे कहाँ से हैं और क्या ढूँढ रहे थे।",
    },
    {  # 6. watches card 6 -- who is around
        "ar": "أي الهواتف والحواسيب المحمولة موجودة في المنزل، ومتى تكون عادةً،"
              " وكم من الوقت ظلت قيد التشغيل اليوم. الإشارة نفسها تجيب عن \"هل"
              " يوجد أحد في المكتب؟\" وعن \"كم كان مقدار الحضور التقديري على"
              " الشبكة؟\".",
        "es": "Qué teléfonos y portátiles están en casa, cuándo suelen estar, y"
              " cuánto tiempo han estado encendidos hoy. La misma señal responde"
              " a \"¿hay alguien en la oficina?\" y a \"¿cuánta presencia estimada"
              " en la red fue esa?\".",
        "fr": "Quels téléphones et ordinateurs portables sont à la maison,"
              " quand ils y sont d'habitude, et depuis combien de temps ils sont"
              " allumés aujourd'hui. Le même signal répond à «&nbsp;y a-t-il"
              " quelqu'un au bureau&nbsp;?&nbsp;» et à «&nbsp;quelle présence"
              " estimée sur le réseau cela représente-t-il&nbsp;?&nbsp;».",
        "de": "Welche Handys und Laptops zu Hause sind, wann sie es"
              " normalerweise sind, und wie lange sie heute schon an sind."
              " Dasselbe Signal beantwortet „Ist jemand im Büro?“ und „Wie viel"
              " geschätzte Netzwerkpräsenz war das?“.",
        "pt": "Que telemóveis e portáteis estão em casa, quando costumam estar,"
              " e há quanto tempo estão ligados hoje. O mesmo sinal responde a"
              " \"está alguém no escritório?\" e a \"quanta presença estimada na"
              " rede foi essa?\".",
        "it": "Quali telefoni e portatili sono a casa, quando di solito ci"
              " sono, e da quanto tempo sono accesi oggi. Lo stesso segnale"
              " risponde a \"c'è qualcuno in ufficio?\" e a \"quanta presenza"
              " stimata in rete è stata?\".",
        "zh": "哪些手机和笔记本电脑在家、通常什么时候在，以及它们今天已经开了多久。同一个信号既回答\"办公室里有人吗？\"，也回答\"那相当于多少估计的网络在线时长？\"。",
        "hi": "कौन-से फ़ोन और लैपटॉप घर पर हैं, आम तौर पर कब होते हैं, और आज"
              " कितनी देर से चालू हैं। यही संकेत \"क्या ऑफ़िस में कोई है?\" और \"वह"
              " कितनी अनुमानित नेटवर्क उपस्थिति थी?\" दोनों का उत्तर देता है।",
    },
]

PLAIN_P.extend([
    {  # 7. step 1 -- install the sensor
        "ar": "أمر واحد على أي جهاز يبقى قيد التشغيل &mdash; جهاز Pi احتياطي،"
              " أو NAS، أو خادم تديره بالفعل. يرصد من الداخل، وهو المكان"
              " الوحيد الذي تكون فيه الشبكة صادقة.",
        "es": "Un comando en cualquier máquina que se mantenga encendida"
              " &mdash; una Raspberry Pi de repuesto, un NAS, un servidor"
              " que ya tengas. Vigila desde dentro, que es el único sitio"
              " donde una red es honesta.",
        "fr": "Une commande sur n'importe quelle machine qui reste"
              " allumée &mdash; un Pi de rechange, un NAS, un serveur que"
              " vous avez déjà. Il surveille depuis l'intérieur, le seul"
              " endroit où un réseau est honnête.",
        "de": "Ein Befehl auf jeder Maschine, die eingeschaltet bleibt"
              " &mdash; ein Ersatz-Pi, ein NAS, ein Server, den Sie schon"
              " betreiben. Er beobachtet von innen, dem einzigen Ort, an"
              " dem ein Netzwerk ehrlich ist.",
        "pt": "Um comando em qualquer máquina que fique ligada &mdash; um"
              " Pi de reserva, um NAS, um servidor que já tenha. Vigia por"
              " dentro, o único lugar onde uma rede é honesta.",
        "it": "Un comando su qualsiasi macchina che resta accesa &mdash;"
              " un Pi di scorta, un NAS, un server che hai già. Osserva"
              " dall'interno, l'unico posto in cui una rete è onesta.",
        "zh": "在任何一台一直开着的机器上执行一条命令 &mdash; 一台备用的 Pi、一台"
              "NAS，或者你已经在用的服务器。它从内部观察，而那正是网络唯一诚实"
              "的地方。",
        "hi": "किसी भी ऐसी मशीन पर एक कमांड जो हमेशा चालू रहती है &mdash; एक"
              " स्पेयर Pi, एक NAS, या कोई सर्वर जो आप पहले से चला रहे हैं। यह"
              " भीतर से देखता है, और वही एक जगह है जहाँ नेटवर्क सच बोलता है।",
    },
    {  # 8. step 2 -- it reports to the hub
        "ar": "يقوم المستشعر بجولات فحص وفق جدول ويرسل ملخصًا لما وجده. لا"
              " يخرج من الشبكة أي شيء آخر: لا التقاط حزم، لا محتوى ملفات، لا"
              " مرور بيانات مُنقّل إلى أي مكان.",
        "es": "El sensor hace pasadas según un horario y envía un resumen"
              " de lo que encontró. Nada más sale de la red: sin capturas"
              " de paquetes, sin contenido de archivos, sin tráfico"
              " tunelizado a ningún sitio.",
        "fr": "Le capteur fait ses passages selon un planning et envoie un"
              " résumé de ce qu'il a trouvé. Rien d'autre ne quitte le"
              " réseau : aucune capture de paquets, aucun contenu de"
              " fichier, aucun trafic tunnellisé nulle part.",
        "de": "Der Sensor scannt nach Zeitplan und sendet eine"
              " Zusammenfassung dessen, was er gefunden hat. Sonst"
              " verlässt nichts das Netzwerk: keine Paketaufzeichnungen,"
              " keine Dateiinhalte, kein Datenverkehr, der irgendwohin"
              " getunnelt wird.",
        "pt": "O sensor faz varreduras num horário e envia um resumo do"
              " que encontrou. Mais nada sai da rede: sem capturas de"
              " pacotes, sem conteúdo de ficheiros, sem tráfego"
              " encapsulado para lugar nenhum.",
        "it": "Il sensore effettua scansioni secondo una pianificazione e"
              " invia un riepilogo di ciò che ha trovato. Nient'altro"
              " lascia la rete: nessuna cattura di pacchetti, nessun"
              " contenuto di file, nessun traffico incanalato altrove.",
        "zh": "传感器按计划进行扫描，并上报它发现内容的摘要。除此之外没有任何"
              "东西离开这个网络：不抓取数据包，不读取文件内容，也不会把流量"
              "隧道传输到任何地方。",
        "hi": "सेंसर एक तय शेड्यूल पर स्कैन करता है और जो पाया उसका सार भेज"
              " देता है। नेटवर्क से और कुछ बाहर नहीं जाता: कोई पैकेट कैप्चर"
              " नहीं, कोई फ़ाइल कंटेंट नहीं, कहीं भी टनल की गई ट्रैफ़िक नहीं।",
    },
    {  # 9. step 3 -- dashboard and an alert
        "ar": "صفحة واحدة للصورة الكاملة، وتنبيه عندما يتغيّر شيء فعليًا"
              " &mdash; جهاز جديد، موقع معطّل، شهادة تنتهي صلاحيتها، منفذ"
              " انفتح.",
        "es": "Una sola página para toda la imagen, y una notificación"
              " cuando algo realmente cambia &mdash; un dispositivo nuevo,"
              " un sitio caído, un certificado que se agota, un puerto que"
              " se abrió.",
        "fr": "Une seule page pour toute la situation, et une notification"
              " quand quelque chose change réellement &mdash; un nouvel"
              " appareil, un site en panne, un certificat qui expire, un"
              " port qui s'est ouvert.",
        "de": "Eine einzige Seite für das Gesamtbild, und eine"
              " Benachrichtigung, wenn sich tatsächlich etwas ändert"
              " &mdash; ein neues Gerät, eine ausgefallene Website, ein"
              " ablaufendes Zertifikat, ein geöffneter Port.",
        "pt": "Uma única página para toda a visão, e uma notificação"
              " quando algo realmente muda &mdash; um dispositivo novo, um"
              " site em baixo, um certificado a esgotar-se, uma porta que"
              " abriu.",
        "it": "Un'unica pagina per l'intero quadro, e una notifica quando"
              " qualcosa cambia davvero &mdash; un nuovo dispositivo, un"
              " sito offline, un certificato in scadenza, una porta che si"
              " è aperta.",
        "zh": "一个页面看到全局情况，一旦真正发生变化就会收到通知 &mdash; 新设备"
              "出现、网站宕机、证书即将过期、某个端口被打开。",
        "hi": "पूरी तस्वीर के लिए एक ही पेज, और असल में कुछ बदलने पर एक"
              " अलर्ट &mdash; एक नया डिवाइस, कोई डाउन साइट, खत्म होता"
              " सर्टिफ़िकेट, या खुला हुआ कोई पोर्ट।",
    },
    {  # 10. fixes card 1 -- fixed list of repairs
        "ar": "يستطيع Vault إعادة تشغيل خدمة توقفت، وتفريغ دليل سجلات ملأ"
              " القرص، وتثبيت تحديثات الحزم المتاحة، أو تفعيل وسائل حماية المضيف"
              " المدعومة. أما تجديد الشهادات فيعتمد على الأدوات المهيأة على"
              " المضيف. هذه القائمة ثابتة ومحصورة مسبقًا: إذا لم يكن الإصلاح"
              " مدرجًا فيها، فلا يستطيع Vault تنفيذه، مهما طُلب منه.",
        "es": "Vault puede reiniciar un servicio que murió, vaciar un"
              " directorio de registros que llenó un disco, instalar las"
              " actualizaciones de paquetes disponibles o activar las"
              " protecciones del host compatibles. La renovación de certificados"
              " depende de las herramientas configuradas en el host. Esa lista"
              " es fija y está en una lista de permitidos: si una reparación no"
              " está en ella, Vault no puede realizarla, por mucho que se le"
              " pida.",
        "fr": "Vault peut redémarrer un service qui s'est arrêté, vider un"
              " répertoire de journaux qui a rempli un disque, installer les"
              " mises à jour de paquets disponibles ou activer les protections"
              " d'hôte prises en charge. Le renouvellement des certificats"
              " dépend des outils configurés sur l'hôte. Cette liste est fixe et"
              " sur liste blanche&nbsp;: si une réparation n'y figure pas,"
              " Vault ne peut pas l'effectuer, quelle que soit la demande.",
        "de": "Vault kann einen abgestürzten Dienst neu starten, ein"
              " Log-Verzeichnis leeren, das eine Festplatte gefüllt hat,"
              " verfügbare Paket-Updates installieren oder unterstützte"
              " Host-Schutzfunktionen aktivieren. Ob ein Zertifikat erneuert"
              " wird, hängt von den auf dem Host eingerichteten Werkzeugen ab."
              " Diese Liste ist fest und freigegeben: Steht eine Reparatur nicht"
              " darauf, kann Vault sie nicht ausführen, wie auch immer er"
              " gefragt wird.",
        "pt": "O Vault pode reiniciar um serviço que parou, esvaziar um"
              " diretório de registos que encheu um disco, instalar as"
              " atualizações de pacotes disponíveis ou ativar as proteções de"
              " anfitrião suportadas. A renovação de certificados depende das"
              " ferramentas configuradas no anfitrião. Essa lista é fixa e está"
              " numa lista de permissões: se uma reparação não constar dela, o"
              " Vault não a pode executar, por mais que lhe peçam.",
        "it": "Vault può riavviare un servizio che si è fermato, svuotare"
              " una directory di log che ha riempito un disco, installare gli"
              " aggiornamenti dei pacchetti disponibili o attivare le protezioni"
              " dell'host supportate. Il rinnovo dei certificati dipende dagli"
              " strumenti configurati sull'host. Quell'elenco è fisso e in lista"
              " consentita: se una riparazione non è presente, Vault non può"
              " eseguirla, comunque gliela si chieda.",
        "zh": "Vault 可以重启已停止的服务、清理占满磁盘的日志目录、安装可用的软件包更新，或启用受支持的主机防护。证书续期则取决于主机上已配置的工具。这份清单是固定的白名单：如果某项修复不在其中，无论如何要求，Vault 都无法执行。",
        "hi": "Vault एक बंद हो चुकी सेवा फिर से चालू कर सकता है, डिस्क भर"
              " देने वाली लॉग डायरेक्टरी खाली कर सकता है, उपलब्ध पैकेज अपडेट"
              " इंस्टॉल कर सकता है, या समर्थित होस्ट सुरक्षा चालू कर सकता है।"
              " सर्टिफ़िकेट नवीनीकरण होस्ट पर कॉन्फ़िगर किए गए टूल पर निर्भर"
              " करता है। यह सूची निश्चित और अनुमति-सूची में है: अगर कोई मरम्मत"
              " इसमें नहीं है, तो चाहे जैसे भी कहा जाए, Vault उसे नहीं कर"
              " सकता।",
    },
    {  # 11. fixes card 2 -- cooldown
        "ar": "يتم تنفيذ الإصلاح، ثم يُقفل ذلك الإصلاح لفترة. خدمة تتعطّل"
              " كل تسعين ثانية تُعاد تشغيلها مرة واحدة ثم تُصعَّد إليك &mdash;"
              " لأن إعادة التشغيل الثانية لم تكن الحل أبدًا، وأداة تستمر في"
              " المحاولة تُخفي العطل الحقيقي.",
        "es": "Se ejecuta una reparación, y luego esa reparación queda"
              " bloqueada durante un tiempo. Un servicio que muere cada"
              " noventa segundos se reinicia una vez y luego se escala a"
              " ti &mdash; porque el segundo reinicio nunca fue la"
              " respuesta, y una herramienta que sigue intentando oculta el"
              " fallo real.",
        "fr": "Une réparation s'exécute, puis cette réparation est"
              " verrouillée pendant un moment. Un service qui plante"
              " toutes les quatre-vingt-dix secondes est redémarré une"
              " fois puis vous est signalé &mdash; parce que le deuxième"
              " redémarrage n'a jamais été la solution, et un outil qui"
              " continue d'essayer masque la vraie panne.",
        "de": "Eine Reparatur läuft, dann ist diese Reparatur für eine"
              " Weile gesperrt. Ein Dienst, der alle neunzig Sekunden"
              " abstürzt, wird einmal neu gestartet und dann an Sie"
              " eskaliert &mdash; denn der zweite Neustart war nie die"
              " Antwort, und ein Tool, das es immer wieder versucht,"
              " verdeckt den eigentlichen Fehler.",
        "pt": "Um reparo é executado, depois esse reparo fica bloqueado"
              " por um tempo. Um serviço que morre a cada noventa segundos"
              " é reiniciado uma vez e depois é escalado para si &mdash;"
              " porque o segundo reinício nunca foi a resposta, e uma"
              " ferramenta que continua a tentar esconde a falha real.",
        "it": "Una riparazione viene eseguita, poi quella riparazione"
              " resta bloccata per un po'. Un servizio che si blocca ogni"
              " novanta secondi viene riavviato una volta e poi viene"
              " segnalato a te &mdash; perché il secondo riavvio non è mai"
              " stata la risposta giusta, e uno strumento che continua a"
              " provare nasconde il vero guasto.",
        "zh": "一次修复运行之后，这项修复会被锁定一段时间。一个每九十秒就崩溃一次"
              "的服务只会被重启一次，然后就上报给你处理 &mdash; 因为第二次重启从来"
              "都不是答案，而一直重试的工具只会掩盖真正的故障。",
        "hi": "एक मरम्मत चलती है, फिर वह मरम्मत कुछ समय के लिए लॉक हो जाती"
              " है। हर नब्बे सेकंड में मर जाने वाली किसी सेवा को एक बार"
              " रीस्टार्ट किया जाता है और फिर आपको बता दिया जाता है &mdash;"
              " क्योंकि दूसरा रीस्टार्ट कभी जवाब नहीं होता, और बार-बार कोशिश"
              " करता टूल असली गड़बड़ी को छिपा देता है।",
    },
    {  # 12. fixes card 3 -- assistant in words
        "ar": "يوجد مساعد على لوحة التحكم يمكنه قراءة كل ما يعرفه Vault"
              " وتشغيل تلك الإصلاحات نفسها. اسأله عن سبب بطء إنترنت المكتب"
              " الليلة الماضية، أو أخبره بإعادة تشغيل الشيء العالق.",
        "es": "Hay un asistente en el panel que puede leer todo lo que"
              " sabe Vault y ejecutar esas mismas reparaciones."
              " Pregúntale por qué el internet de la oficina estuvo lento"
              " anoche, o dile que reinicie lo que está atascado.",
        "fr": "Il y a un assistant sur le tableau de bord qui peut lire"
              " tout ce que Vault sait et exécuter ces mêmes"
              " réparations. Demandez-lui pourquoi Internet était lent au"
              " bureau hier soir, ou dites-lui de redémarrer ce qui est"
              " bloqué.",
        "de": "Es gibt einen Assistenten im Dashboard, der alles lesen"
              " kann, was Vault weiß, und dieselben Reparaturen"
              " ausführen kann. Fragen Sie ihn, warum das Büro-Internet"
              " letzte Nacht langsam war, oder sagen Sie ihm, er soll das"
              " hängende Ding neu starten.",
        "pt": "Há um assistente no painel que consegue ler tudo o que o"
              " Vault sabe e executar esses mesmos reparos."
              " Pergunte-lhe por que a internet do escritório esteve lenta"
              " ontem à noite, ou diga-lhe para reiniciar o que está"
              " preso.",
        "it": "C'è un assistente sulla dashboard che può leggere tutto ciò"
              " che Vault sa e avviare le stesse riparazioni. Chiedigli"
              " perché ieri notte internet in ufficio era lento, o digli"
              " di riavviare la cosa che è bloccata.",
        "zh": "仪表盘上有一个助手，它能读取 Vault 掌握的一切信息，并执行同样的"
              "修复操作。你可以问它昨晚办公室网络为什么慢，或者让它重启那个卡住"
              "的东西。",
        "hi": "डैशबोर्ड पर एक असिस्टेंट है जो Vault की सारी जानकारी पढ़"
              " सकता है और वही मरम्मत चला सकता है। उससे पूछें कि पिछली रात"
              " ऑफ़िस का इंटरनेट धीमा क्यों था, या उसे कहें कि जो चीज़ अटकी है"
              " उसे रीस्टार्ट कर दे।",
    },
    {  # 13. fixes card 4 -- asks first when it matters
        "ar": "أي شيء مُعطِّل &mdash; إعادة تشغيل، فصل جهاز عن الشبكة &mdash;"
              " يُقترح، ولا يُنفَّذ. ترى بالضبط ما ينوي فعله وتوافق. كل"
              " إجراء، تلقائيًا أو بطلب، يُسجَّل مع من أو ما الذي أطلقه.",
        "es": "Cualquier cosa disruptiva &mdash; un reinicio, desconectar"
              " un dispositivo de la red &mdash; se propone, no se"
              " ejecuta. Ves exactamente qué pretende hacer y das tu"
              " visto bueno. Cada acción, automática o solicitada, queda"
              " registrada con quién o qué la activó.",
        "fr": "Tout ce qui est perturbateur &mdash; un redémarrage, couper"
              " un appareil du réseau &mdash; est proposé, pas exécuté."
              " Vous voyez exactement ce qu'il compte faire et vous dites"
              " oui. Chaque action, automatique ou demandée, est"
              " consignée avec qui ou quoi l'a déclenchée.",
        "de": "Alles Störende &mdash; ein Neustart, ein Gerät vom Netzwerk"
              " trennen &mdash; wird vorgeschlagen, nicht ausgeführt. Sie"
              " sehen genau, was es vorhat, und stimmen zu. Jede Aktion,"
              " automatisch oder angefragt, wird zusammen mit dem"
              " Auslöser protokolliert.",
        "pt": "Qualquer coisa disruptiva &mdash; um reinício, desligar um"
              " dispositivo da rede &mdash; é proposta, não executada."
              " Você vê exatamente o que pretende fazer e diz sim. Cada"
              " ação, automática ou pedida, é registada com quem ou o que"
              " a desencadeou.",
        "it": "Qualsiasi cosa dirompente &mdash; un riavvio, tagliare un"
              " dispositivo fuori dalla rete &mdash; viene proposta, non"
              " eseguita. Vedi esattamente cosa intende fare e dai il tuo"
              " ok. Ogni azione, automatica o richiesta, viene registrata"
              " con chi o cosa l'ha innescata.",
        "zh": "任何有破坏性的操作 &mdash; 重启、把某个设备踢下网络 &mdash; 都只会"
              "被提出建议，而不会直接执行。你会准确看到它打算做什么，然后自己"
              "同意。每一个动作，无论是自动执行还是你要求的，都会记录下是谁或"
              "什么触发了它。",
        "hi": "कोई भी बड़ा बदलाव &mdash; रीबूट, किसी डिवाइस को नेटवर्क से"
              " काटना &mdash; सुझाया जाता है, किया नहीं जाता। आप ठीक-ठीक"
              " देखते हैं कि वह क्या करने वाला है और फिर हाँ कहते हैं। हर"
              " कार्रवाई, चाहे वह खुद हुई हो या माँगी गई हो, इस बात के साथ"
              " दर्ज होती है कि उसे किसने या किस चीज़ ने शुरू किया।",
    },
])

PLAIN_P.extend([
    {  # 13b. capabilities table footnote -- the scope caveat under "What
       #      Vault can do today". Sits between the repair-policy cards and
       #      the trial copy, so it must stay FIRST in this block: PLAIN_P is
       #      matched sequentially against the document, not by content.
        "ar": "تعتمد الفحوص والإجراءات المتاحة على إمكانية الوصول إلى"
              " المستشعر، وبيانات الاعتماد المهيأة، ودعم كل منصة. الإعداد"
              " يدوي في الوقت الحالي، ولا يوجد دفع ذاتي.",
        "es": "Las comprobaciones y acciones disponibles dependen de si el"
              " sensor es accesible, de las credenciales configuradas y de"
              " la compatibilidad de cada plataforma. La configuración es"
              " manual por ahora; no hay pago en autoservicio.",
        "fr": "Les vérifications et les actions disponibles dépendent de"
              " l'accessibilité du capteur, des identifiants configurés et"
              " de la prise en charge de chaque plateforme. L'installation"
              " est manuelle pour l'instant&nbsp;; il n'y a pas de paiement"
              " en libre-service.",
        "de": "Welche Prüfungen und Aktionen verfügbar sind, hängt davon ab,"
              " ob der Sensor erreichbar ist, welche Zugangsdaten hinterlegt"
              " sind und was die jeweilige Plattform unterstützt. Die"
              " Einrichtung erfolgt derzeit manuell; einen"
              " Self-Service-Checkout gibt es nicht.",
        "pt": "As verificações e ações disponíveis dependem de o sensor"
              " estar acessível, das credenciais configuradas e do suporte"
              " de cada plataforma. A configuração é manual por agora; não"
              " existe pagamento em autosserviço.",
        "it": "I controlli e le azioni disponibili dipendono dalla"
              " raggiungibilità del sensore, dalle credenziali configurate e"
              " dal supporto della piattaforma. La configurazione è manuale"
              " per ora; non è attivo il pagamento self-service.",
        "zh": "可用的检查和操作取决于传感器是否可达、已配置的凭据，以及各平台的支持情况。"
              "目前设置需要手动完成，尚未开放自助结账。",
        "hi": "कौन-सी जाँच और कार्रवाइयाँ उपलब्ध हैं, यह सेंसर की पहुँच,"
              " कॉन्फ़िगर किए गए क्रेडेंशियल और प्लैटफ़ॉर्म समर्थन पर निर्भर"
              " करता है। सेटअप अभी मैन्युअल है; सेल्फ़-सर्विस चेकआउट उपलब्ध"
              " नहीं है।",
    },
    {  # 14. trial p1
        'ar': 'تحصل على المنتج كاملًا من اليوم الأول &mdash; المستشعر ولوحة التحكم والتنبيهات والإصلاحات التلقائية &mdash; لمدة ثلاثة أشهر، دون تسجيل أي وسيلة دفع.',
        'es': 'Tienes el producto completo desde el primer día &mdash; el sensor, el panel, las alertas y las reparaciones automáticas &mdash; durante tres meses, sin ningún método de pago registrado.',
        'fr': 'Vous avez le produit complet dès le premier jour &mdash; le capteur, le tableau de bord, les alertes et les réparations automatiques &mdash; pendant trois mois, sans moyen de paiement enregistré.',
        'de': 'Sie bekommen vom ersten Tag an das ganze Produkt &mdash; Sensor, Dashboard, Benachrichtigungen und automatische Reparaturen &mdash; drei Monate lang, ohne hinterlegtes Zahlungsmittel.',
        'pt': 'Você tem o produto completo desde o primeiro dia &mdash; o sensor, o painel, os alertas e as reparações automáticas &mdash; por três meses, sem nenhum meio de pagamento registrado.',
        'it': 'Hai il prodotto completo dal primo giorno &mdash; il sensore, la dashboard, gli avvisi e le riparazioni automatiche &mdash; per tre mesi, senza alcun metodo di pagamento registrato.',
        'zh': '从第一天起你就拥有完整产品 &mdash; 传感器、仪表盘、告警和自动修复 &mdash; 为期三个月，无需预留任何支付方式。',
        'hi': 'पहले दिन से आपको पूरा प्रोडक्ट मिलता है &mdash; सेंसर, डैशबोर्ड, अलर्ट और ऑटोमैटिक मरम्मत &mdash; तीन महीने तक, बिना कोई भुगतान विधि दर्ज किए।',
    },
    {  # 15. trial p2
        'ar': 'في النهاية لا يُحصّل شيء، لأنه لم يُسجّل أصلًا ما يمكن تحصيله. يتوقف الحساب مؤقتًا: كل ما جُمع يبقى قابلًا للقراءة والتصدير، وإن أردت الاستمرار نرتّب ذلك معك مباشرة.',
        'es': 'Al final no se cobra nada, porque nunca se registró nada que cobrar. La cuenta se pausa: todo lo ya recogido sigue siendo legible y exportable, y si quieres continuar lo resolvemos contigo directamente.',
        'fr': "À la fin, rien n'est facturé, parce que rien n'a jamais été enregistré à facturer. Le compte se met en pause : tout ce qui a été collecté reste lisible et exportable, et si vous voulez continuer, nous réglons cela directement avec vous.",
        'de': 'Am Ende wird nichts berechnet, weil nie etwas zum Abbuchen erfasst wurde. Das Konto pausiert: alles bereits Gesammelte bleibt lesbar und exportierbar, und wenn Sie weitermachen wollen, klären wir das direkt mit Ihnen.',
        'pt': 'No fim nada é cobrado, porque nunca houve nada registrado para cobrar. A conta pausa: tudo o que já foi coletado continua legível e exportável, e se quiser continuar resolvemos isso diretamente com você.',
        'it': "Alla fine non viene addebitato nulla, perché non è mai stato raccolto nulla da addebitare. L'account va in pausa: tutto ciò che è già stato raccolto resta leggibile ed esportabile, e se vuoi continuare lo sistemiamo direttamente con te.",
        'zh': '到期时不会扣任何费用，因为从未登记过可扣费的信息。账户将暂停：已收集的一切仍可查看和导出；如果你想继续，我们会直接与你商定。',
        'hi': 'अंत में कुछ भी चार्ज नहीं होता, क्योंकि चार्ज करने लायक कुछ कभी लिया ही नहीं गया। खाता रुक जाता है: अब तक जो जमा हुआ है वह पढ़ने और एक्सपोर्ट करने के लिए बना रहता है, और अगर आप जारी रखना चाहें तो हम आपसे सीधे तय कर लेंगे।',
    },
])

PLAIN_P.extend([
    {  # 21. faq1 answer -- what can the sensor see
        "ar": "ما يمكن لجهاز على شبكتك رؤيته بأدب: أي عناوين تستجيب وما"
              " الأجهزة التي تدّعي أنها هي، أي منافذ عليها مفتوحة، هل تستجيب"
              " مواقعك وخوادمك، و &mdash; إذا وجّهت DNS إلى Vault &mdash;"
              " الأسماء التي بُحث عنها وبواسطة أي جهاز. لا يقرأ حركة بياناتك،"
              " ولا يلتقط حزمًا، ولا يفتح ملفات. على الخوادم تصله عبر وصول"
              " SSH خاص بك ويشغّل فحوصات قراءة فقط، بالإضافة إلى الإصلاحات"
              " التي تسمح بها بالضبط.",
        "es": "Lo que puede ver un dispositivo en tu red preguntando"
              " educadamente: qué direcciones responden y qué hardware"
              " dicen ser, qué puertos tienen abiertos, si tus sitios y"
              " servidores responden, y &mdash; si apuntas tu DNS a"
              " Vault &mdash; los nombres que se buscaron y desde qué"
              " dispositivo. No lee tu tráfico, no captura paquetes, ni"
              " abre archivos. En los servidores se conecta con tu propio"
              " acceso SSH y ejecuta comprobaciones de solo lectura, más"
              " exactamente las reparaciones que permitas.",
        "fr": "Ce qu'un appareil sur votre réseau peut voir en demandant"
              " poliment : quelles adresses répondent et quel matériel"
              " elles prétendent être, quels ports sont ouverts sur"
              " elles, si vos sites et serveurs répondent, et &mdash; si"
              " vous dirigez votre DNS vers Vault &mdash; les noms"
              " recherchés et par quel appareil. Il ne lit pas votre"
              " trafic, ne capture pas de paquets, n'ouvre pas de"
              " fichiers. Sur les serveurs, il se connecte via votre"
              " propre accès SSH et exécute des vérifications en lecture"
              " seule, plus exactement les réparations que vous autorisez.",
        "de": "Was ein Gerät in Ihrem Netzwerk sehen kann, indem es"
              " freundlich fragt: welche Adressen antworten und welche"
              " Hardware sie zu sein behaupten, welche Ports auf ihnen"
              " offen sind, ob Ihre Websites und Server antworten, und"
              " &mdash; wenn Sie Ihr DNS auf Vault richten &mdash; die"
              " Namen, die nachgeschlagen wurden, und von welchem Gerät."
              " Es liest nicht Ihren Datenverkehr, erfasst keine Pakete"
              " und öffnet keine Dateien. Auf Servern verbindet es sich"
              " über Ihren eigenen SSH-Zugang und führt reine"
              " Lesetests durch, plus genau die Reparaturen, die Sie"
              " erlauben.",
        "pt": "O que um dispositivo na sua rede pode ver perguntando"
              " educadamente: que endereços respondem e que hardware"
              " dizem ser, que portas neles estão abertas, se os seus"
              " sites e servidores respondem, e &mdash; se apontar o seu"
              " DNS para o Vault &mdash; os nomes que foram pesquisados"
              " e por qual dispositivo. Não lê o seu tráfego, não captura"
              " pacotes, nem abre ficheiros. Nos servidores, liga-se pelo"
              " seu próprio acesso SSH e executa verificações só de"
              " leitura, mais exatamente os reparos que você permitir.",
        "it": "Ciò che un dispositivo sulla tua rete può vedere chiedendo"
              " gentilmente: quali indirizzi rispondono e quale hardware"
              " dichiarano di essere, quali porte su di essi sono aperte,"
              " se i tuoi siti e server rispondono, e &mdash; se punti il"
              " tuo DNS verso Vault &mdash; i nomi che sono stati"
              " ricercati e da quale dispositivo. Non legge il tuo"
              " traffico, non catturas pacchetti, né apre file. Sui"
              " server si collega tramite il tuo stesso accesso SSH ed"
              " esegue controlli in sola lettura, più esattamente le"
              " riparazioni che consenti.",
        "zh": "你网络上的一台设备通过\"礼貌地询问\"能看到什么：哪些地址会响应、"
              "它们声称自己是什么设备、它们开放了哪些端口、你的站点和服务器是否"
              "响应，以及 &mdash; 如果你把 DNS 指向 Vault &mdash; 被查询过"
              "的域名以及是哪台设备查询的。它不会读取你的流量、不会抓取数据包，"
              "也不会打开文件。在服务器上，它通过你自己的 SSH 访问权限连接，只"
              "执行只读检查，外加你明确允许的那些修复操作。",
        "hi": "आपके नेटवर्क पर एक डिवाइस विनम्रता से पूछकर क्या देख सकता है:"
              " कौन-से पते जवाब देते हैं और वे किस हार्डवेयर होने का दावा"
              " करते हैं, उनके कौन-से पोर्ट खुले हैं, क्या आपकी साइट और"
              " सर्वर जवाब देते हैं, और &mdash; अगर आप अपना DNS Vault की"
              " ओर पॉइंट करें &mdash; कौन-से नाम खोजे गए और किस डिवाइस ने"
              " खोजे। यह आपका ट्रैफ़िक नहीं पढ़ता, पैकेट कैप्चर नहीं करता, और"
              " फ़ाइलें नहीं खोलता। सर्वर पर यह आपकी अपनी SSH एक्सेस से जुड़ता"
              " है और सिर्फ़ रीड-ओनली जाँच चलाता है, साथ में वही मरम्मतें"
              " जिन्हें आप ठीक-ठीक अनुमति देते हैं।",
    },
    {  # 22. faq2 answer -- where does the data live
        "ar": "في الخادم المركزي الخاص بحسابك، وليس في أي مكان آخر &mdash;"
              " لا يُباع، ولا يُجمَع مع بيانات أي شخص آخر، ولا يُستخدم لتدريب"
              " أي شيء. ما يخرج من شبكتك هو ملخص المستشعر لما وجده، عبر"
              " HTTPS. استضافة الخادم المركزي على جهازك ممكنة، واليوم هي"
              " شيء نُجهّزه معك بدلًا من أن يكون تنزيلًا.",
        "es": "En el hub de tu cuenta, y en ningún otro sitio &mdash; no"
              " se vende, no se agrupa con el de nadie más, y no se usa"
              " para entrenar nada. Lo que sale de tu red es el resumen"
              " del sensor de lo que encontró, por HTTPS. Alojar el hub"
              " en tu propia máquina es posible, y hoy es algo que"
              " configuramos contigo en vez de una descarga.",
        "fr": "Dans le hub de votre compte, et nulle part ailleurs"
              " &mdash; elles ne sont ni vendues, ni regroupées avec"
              " celles de quelqu'un d'autre, ni utilisées pour entraîner"
              " quoi que ce soit. Ce qui quitte votre réseau, c'est le"
              " résumé du capteur de ce qu'il a trouvé, via HTTPS."
              " Auto-héberger le hub sur votre propre machine est"
              " possible, et aujourd'hui c'est quelque chose que nous"
              " mettons en place avec vous plutôt qu'un téléchargement.",
        "de": "Im Hub für Ihr Konto, und nirgendwo sonst &mdash; sie"
              " werden nicht verkauft, nicht mit denen anderer"
              " zusammengeführt, und nicht zum Trainieren irgendetwas"
              " verwendet. Was Ihr Netzwerk verlässt, ist die"
              " Zusammenfassung des Sensors über das, was er gefunden hat,"
              " per HTTPS. Den Hub selbst auf Ihrer eigenen Maschine zu"
              " hosten ist möglich, und heute ist das etwas, das wir mit"
              " Ihnen gemeinsam einrichten, statt es zum Download"
              " anzubieten.",
        "pt": "No hub da sua conta, e em nenhum outro lugar &mdash; não é"
              " vendido, não é agrupado com o de mais ninguém, e não é"
              " usado para treinar nada. O que sai da sua rede é o resumo"
              " do sensor sobre o que encontrou, via HTTPS. Hospedar o hub"
              " na sua própria máquina é possível, e hoje é algo que"
              " configuramos consigo em vez de ser um download.",
        "it": "Nel hub del tuo account, e in nessun altro posto &mdash;"
              " non viene venduto, non viene raggruppato con quello di"
              " altri, e non viene usato per addestrare nulla. Ciò che"
              " esce dalla tua rete è il riepilogo del sensore su ciò che"
              " ha trovato, via HTTPS. Ospitare tu stesso il hub sulla tua"
              " macchina è possibile, e oggi è qualcosa che configuriamo"
              " insieme a te piuttosto che un download.",
        "zh": "只存放在你账户的中枢里，不会在别处 &mdash; 不会被出售，不会与其他"
              "任何人的数据混在一起，也不会被用来训练任何模型。离开你网络的只有"
              "传感器通过 HTTPS 上报的发现摘要。在自己的机器上自行搭建中枢也是"
              "可行的，目前这需要我们和你一起配置完成，而不是提供一个下载包。",
        "hi": "आपके अकाउंट के हब में, और कहीं और नहीं &mdash; इसे न बेचा"
              " जाता है, न किसी और के डेटा के साथ मिलाया जाता है, और न"
              " किसी चीज़ को ट्रेन करने के लिए इस्तेमाल किया जाता है। आपके"
              " नेटवर्क से जो बाहर जाता है वह सेंसर द्वारा HTTPS पर भेजा गया"
              " निष्कर्षों का सार है। हब को अपनी ही मशीन पर सेल्फ़-होस्ट करना"
              " संभव है, और आज यह डाउनलोड करने वाली चीज़ नहीं, बल्कि हम आपके"
              " साथ मिलकर सेट करते हैं।",
    },
    {  # 23. faq3 answer -- hub unreachable
        "ar": "لا شيء. المستشعر ليس في مسار حركة بياناتك &mdash; يراقب من"
              " الجانب، فإن توقف، أو تعطّل الخادم المركزي، أو انقطع اتصالك،"
              " تستمر شبكتك تمامًا كما كانت. يستمر المستشعر في الفحص ويرسل"
              " ما جمعه بمجرد أن يتمكن من الوصول إلى الخادم المركزي مجددًا."
              " الاستثناء الوحيد هو تصفية DNS، وهي اختيارية ولا تكون في"
              " المسار إلا إذا وجّهت أجهزتك إليها؛ فتستمر بالإجابة من ذاكرتها"
              " المؤقتة وقوائمها، دون الحاجة إلى الخادم المركزي.",
        "es": "Nada. El sensor no está en el camino de tu tráfico &mdash;"
              " vigila desde el lado, así que si se detiene, o el hub"
              " está caído, o tu conexión se corta, tu red sigue"
              " funcionando exactamente igual que antes. El sensor sigue"
              " haciendo pasadas y envía lo que recopiló en cuanto pueda"
              " volver a alcanzar el hub. La única excepción es el"
              " filtrado de DNS, que es opcional y solo está en el camino"
              " si decides apuntar tus dispositivos hacia él; sigue"
              " respondiendo desde su propia caché y listas, sin el hub.",
        "fr": "Rien. Le capteur n'est pas dans le chemin de votre trafic"
              " &mdash; il surveille depuis le côté, donc s'il s'arrête,"
              " ou si le hub est en panne, ou si votre connexion est"
              " coupée, votre réseau continue exactement comme avant. Le"
              " capteur continue ses passages et envoie ce qu'il a"
              " collecté dès qu'il peut de nouveau atteindre le hub. La"
              " seule exception est le filtrage DNS, qui est optionnel et"
              " n'est dans le chemin que si vous choisissez d'y diriger"
              " vos appareils ; il continue de répondre depuis son propre"
              " cache et ses propres listes, sans le hub.",
        "de": "Nichts. Der Sensor liegt nicht im Pfad Ihres"
              " Datenverkehrs &mdash; er beobachtet von der Seite, also"
              " läuft Ihr Netzwerk, falls er stoppt, der Hub ausfällt oder"
              " Ihre Verbindung ausfällt, genau wie zuvor weiter. Der"
              " Sensor scannt weiter und sendet, was er gesammelt hat,"
              " sobald er den Hub wieder erreichen kann. Die einzige"
              " Ausnahme ist die DNS-Filterung, die optional ist und nur"
              " im Pfad liegt, wenn Sie Ihre Geräte darauf ausrichten; sie"
              " antwortet weiter aus ihrem eigenen Cache und ihren Listen,"
              " ohne den Hub.",
        "pt": "Nada. O sensor não está no caminho do seu tráfego &mdash;"
              " vigia pelo lado, por isso, se ele parar, ou o hub estiver"
              " em baixo, ou o seu link cair, a sua rede continua"
              " exatamente como antes. O sensor continua a fazer"
              " varreduras e envia o que recolheu assim que conseguir"
              " alcançar o hub novamente. A única excepção é a filtragem"
              " de DNS, que é opcional e só está no caminho se optar por"
              " apontar os seus dispositivos para ela; continua a"
              " responder a partir da sua própria cache e listas, sem o"
              " hub.",
        "it": "Niente. Il sensore non è nel percorso del tuo traffico"
              " &mdash; osserva di lato, quindi se si ferma, o il hub è"
              " offline, o la tua connessione è interrotta, la tua rete"
              " continua esattamente come prima. Il sensore continua a"
              " fare scansioni e invia ciò che ha raccolto non appena"
              " riesce a raggiungere di nuovo il hub. L'unica eccezione è"
              " il filtro DNS, che è opzionale ed è nel percorso solo se"
              " scegli di puntarvi i tuoi dispositivi; continua a"
              " rispondere dalla sua cache e dalle sue liste, senza il"
              " hub.",
        "zh": "什么都不会发生。传感器并不处在你流量的路径上 &mdash; 它在旁边观察，"
              "所以即便它停止工作、中枢宕机，或者你的上行链路中断，你的网络也会"
              "完全照常运行。传感器会继续扫描，并在能重新连上中枢后把收集到的"
              "内容发送出去。唯一的例外是 DNS 过滤功能，它是可选的，只有当你把"
              "设备指向它时才会处在路径中；即使没有中枢，它也会继续用自己的缓存"
              "和列表来响应查询。",
        "hi": "कुछ नहीं होता। सेंसर आपके ट्रैफ़िक के रास्ते में नहीं है"
              " &mdash; यह किनारे से देखता है, इसलिए अगर यह रुक जाए, या हब"
              " डाउन हो जाए, या आपका अपलिंक टूट जाए, तो भी आपका नेटवर्क"
              " पहले की तरह ही चलता रहता है। सेंसर स्कैन करता रहता है और"
              " जैसे ही हब तक फिर पहुँच बनती है, वह जो जुटाया था वह भेज देता"
              " है। एकमात्र अपवाद DNS फ़िल्टरिंग है, जो वैकल्पिक है और तभी"
              " रास्ते में आती है जब आप अपने डिवाइस उसकी ओर पॉइंट करने का"
              " चुनाव करते हैं; यह बिना हब के भी अपने ही कैश और सूचियों से"
              " जवाब देती रहती है।",
    },
    {  # 24. faq4 answer -- what does it cost
        'ar': 'لا شيء لمدة ثلاثة أشهر، ولا يوجد نموذج بطاقة في أي مكان على هذا الموقع. لا توجد خطط للاختيار بينها حاليًا &mdash; أخبرنا بما تعتني به، ونجهّز التثبيت يدويًا، وتستخدم المنتج كاملًا. وإن تغيّر ذلك يومًا، فستُخبَر قبل حدوثه لا بعده.',
        'es': 'Nada durante tres meses, y no hay formulario de tarjeta en ninguna parte de este sitio. Ahora mismo no hay planes entre los que elegir &mdash; cuéntanos qué cuidas, configuramos la instalación a mano y usas el producto completo. Si eso cambiara alguna vez, te lo diríamos antes, no después.',
        'fr': "Rien pendant trois mois, et il n'y a aucun formulaire de carte sur ce site. Il n'y a pour l'instant aucune offre à choisir &mdash; dites-nous ce dont vous vous occupez, nous installons à la main, et vous utilisez tout le produit. Si cela devait changer, vous le sauriez avant, pas après.",
        'de': 'Drei Monate lang nichts, und auf dieser Seite gibt es nirgends ein Kartenformular. Es gibt derzeit keine Pläne zur Auswahl &mdash; sagen Sie uns, was Sie betreuen, wir richten die Installation von Hand ein, und Sie nutzen das ganze Produkt. Sollte sich das je ändern, erfahren Sie es vorher, nicht nachher.',
        'pt': 'Nada por três meses, e não há formulário de cartão em lugar nenhum deste site. No momento não há planos para escolher &mdash; conte-nos o que você cuida, configuramos a instalação manualmente e você usa o produto inteiro. Se isso um dia mudar, você será avisado antes, não depois.',
        'it': "Niente per tre mesi, e su questo sito non c'è alcun modulo per la carta. Al momento non ci sono piani tra cui scegliere &mdash; dicci di cosa ti occupi, configuriamo l'installazione a mano e usi l'intero prodotto. Se mai dovesse cambiare, lo saprai prima, non dopo.",
        'zh': '三个月内免费，本站任何地方都没有银行卡表单。目前没有套餐可供选择 &mdash; 告诉我们你负责哪些网络，我们手动为你部署，你就能使用完整产品。如果将来有变化，我们会提前告知，而不是事后。',
        'hi': 'तीन महीने तक कुछ नहीं, और इस साइट पर कहीं भी कार्ड फ़ॉर्म नहीं है। अभी चुनने के लिए कोई प्लान नहीं है &mdash; हमें बताएँ आप क्या संभालते हैं, हम हाथ से सेटअप करते हैं, और आप पूरा प्रोडक्ट इस्तेमाल करते हैं। अगर यह कभी बदला, तो आपको पहले बताया जाएगा, बाद में नहीं।',
    },
    {  # 25. faq5 answer -- how to cancel
        'ar': 'خلال الأشهر الثلاثة لا شيء لإلغائه: لم تُؤخذ بطاقة ولم تبدأ أي خطة. أخبرنا أنك انتهيت وسيُزال التثبيت؛ وكل ما جُمع ملكك ويمكنك تصديره أولًا.',
        'es': 'Durante los tres meses no hay nada que cancelar: no se tomó tarjeta ni se inició plan alguno. Dinos que has terminado y retiramos la instalación; todo lo recogido es tuyo para exportarlo antes.',
        'fr': "Pendant les trois mois, il n'y a rien à résilier : aucune carte n'a été prise et aucune offre souscrite. Dites-nous que c'est terminé et le déploiement est retiré ; tout ce qui a été collecté vous appartient et peut être exporté avant.",
        'de': 'Während der drei Monate gibt es nichts zu kündigen: keine Karte wurde hinterlegt und kein Plan gestartet. Sagen Sie uns Bescheid, und die Installation wird entfernt; alles Gesammelte können Sie vorher exportieren.',
        'pt': 'Durante os três meses não há nada a cancelar: nenhum cartão foi registrado e nenhum plano iniciado. Avise que terminou e a instalação é retirada; tudo o que foi coletado é seu para exportar antes.',
        'it': "Durante i tre mesi non c'è nulla da disdire: nessuna carta è stata presa e nessun piano avviato. Dicci che hai finito e l'installazione viene rimossa; tutto ciò che è stato raccolto è tuo e puoi esportarlo prima.",
        'zh': '三个月期间没有什么需要取消：没有登记银行卡，也没有开通任何套餐。告诉我们你结束了，部署就会被移除；已收集的数据都归你，可先行导出。',
        'hi': 'तीन महीनों के दौरान रद्द करने के लिए कुछ नहीं है: न कोई कार्ड लिया गया, न कोई प्लान शुरू हुआ। बता दें कि आपका काम पूरा हुआ, और सेटअप हटा दिया जाएगा; जमा हुआ सारा डेटा आपका है, पहले एक्सपोर्ट कर लें।',
    },
])



def build(locale: str) -> str:
    doc = SOURCE.read_text(encoding="utf-8")

    # Longest-fragment-first: guards against a short fragment's English text
    # accidentally sitting inside a longer fragment that has not been
    # translated yet (see the LITERAL block comment).
    for key in sorted(LITERAL, key=lambda k: -len(LITERAL[k]["en"])):
        variants = LITERAL[key]
        en = variants["en"]
        assert en in doc, f"LITERAL[{key!r}] not found in site/index.html -- English source moved"
        doc = doc.replace(en, variants[locale])

    for pattern, variants in ONE_OFF:
        translated = variants[locale]
        new_doc, n = re.subn(
            pattern,
            lambda m, t=translated: m.group(1) + t + m.group(3),
            doc, count=1, flags=re.S,
        )
        assert n == 1, f"ONE_OFF pattern matched {n} times, expected 1: {pattern[:60]}"
        doc = new_doc

    def run_sequential(pattern, group_list, label):
        nonlocal doc
        # ONE re.sub call across the whole document, not N calls of
        # count=1: the translated replacement keeps the SAME opening/
        # closing tag as the English original (that is the whole point --
        # it preserves the class="lede"/"cta-note" styling), which means
        # a second, separate re.subn(..., count=1) call would match that
        # already-translated paragraph again -- its tag looks identical to
        # an untranslated one -- and overwrite it with the NEXT item,
        # while the real next occurrence is never reached. A counter
        # closure over one single-pass re.sub does not have this problem:
        # the regex engine walks the string once, left to right, and calls
        # the replacement function on each match as it is found, never
        # re-visiting text substituted earlier in the same call.
        counter = [0]

        def repl(m):
            i = counter[0]
            counter[0] += 1
            assert i < len(group_list), (
                f"{label}: more matches in the document than translations "
                f"({len(group_list)}) -- a paragraph was added without a "
                f"matching entry in the {label} list"
            )
            return m.group(1) + group_list[i][locale] + m.group(3)

        doc = re.sub(pattern, repl, doc, flags=re.S)
        assert counter[0] == len(group_list), (
            f"{label}: expected {len(group_list)} matches, found {counter[0]} "
            "-- a paragraph was removed, or the order in site/index.html "
            "changed, without updating this list"
        )

    run_sequential(LEDE_PATTERN, LEDE, "LEDE")
    run_sequential(CTA_NOTE_PATTERN, CTA_NOTE, "CTA_NOTE")
    run_sequential(PLAIN_P_PATTERN, PLAIN_P, "PLAIN_P")

    # <html lang> + dir="rtl" for Arabic only.
    dir_attr = ' dir="rtl"' if locale in RTL_LOCALES else ""
    doc = doc.replace('<html lang="en">', f'<html lang="{locale}"{dir_attr}>')

    # Canonical link points at this locale's own URL.
    doc = doc.replace(
        '<link rel="canonical" href="https://guarder.io/">',
        f'<link rel="canonical" href="https://guarder.io/{locale}/">',
    )

    # Footer language switcher: move aria-current="page" off English and
    # onto this locale's own link.
    doc = doc.replace(
        '<a href="/" hreflang="en" lang="en" data-lang="en" aria-current="page">English</a>',
        '<a href="/" hreflang="en" lang="en" data-lang="en">English</a>',
    )
    hreflang_attr = locale  # the footer's own <a hreflang> stays short-form
    old_link = f'<a href="/{locale}/" hreflang="{hreflang_attr}" lang="{locale}" data-lang="{locale}">{NATIVE_LABEL[locale]}</a>'
    new_link = f'<a href="/{locale}/" hreflang="{hreflang_attr}" lang="{locale}" data-lang="{locale}" aria-current="page">{NATIVE_LABEL[locale]}</a>'
    assert old_link in doc, f"footer language link for {locale!r} not found -- check NATIVE_LABEL / the footer markup"
    doc = doc.replace(old_link, new_link)

    return doc


def main() -> int:
    for locale in LOCALES:
        out_dir = SITE / locale
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / "index.html"
        out_file.write_text(build(locale), encoding="utf-8")
        print(f"wrote {out_file.relative_to(SITE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

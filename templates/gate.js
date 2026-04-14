const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PASSWORD = process.env.ECHOBOX_REPORT_PASSWORD || 'ECHOBOX_DEFAULT_PASSWORD';
if (!PASSWORD || PASSWORD.length < 4) {
    console.error('ERROR: No password set. Set ECHOBOX_REPORT_PASSWORD or configure publish.password in echobox.yaml.');
    process.exit(1);
}
const COOKIE_NAME = 'echobox_report';
const COOKIE_MAX_AGE = 86400 * 7;

const HMAC_SECRET = process.env.ECHOBOX_HMAC_SECRET
    || crypto.createHash('sha256').update(`echobox:${PASSWORD}`).digest('hex');

function makeToken(p) {
    return crypto.createHmac('sha256', HMAC_SECRET).update(p).digest('hex').slice(0, 32);
}
const VALID_TOKEN = makeToken(PASSWORD);

function parseCookies(h) {
    const c = {};
    if (!h) return c;
    h.split(';').forEach(s => {
        const [k, ...v] = s.trim().split('=');
        if (k) c[k.trim()] = v.join('=').trim();
    });
    return c;
}

const LOGIN_PAGE = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Call Report</title>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@400;700;800&family=Fragment+Mono&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Bricolage Grotesque',sans-serif;background:#08080c;color:#ece8e0;height:100dvh;display:flex;align-items:center;justify-content:center}
.gate{text-align:center;max-width:360px;padding:0 24px}
h1{font-size:48px;font-weight:800;color:#c62828;letter-spacing:-2px;margin-bottom:4px}
.sub{font-family:'Fragment Mono',monospace;font-size:12px;color:#8a8478;letter-spacing:4px;text-transform:uppercase;margin-bottom:32px}
form{display:flex;flex-direction:column;gap:12px}
input[type=password]{font-family:'Fragment Mono',monospace;font-size:16px;padding:14px 18px;background:#111118;border:1px solid rgba(200,60,60,.15);border-radius:8px;color:#ece8e0;outline:0}
input:focus{border-color:#c62828}
button{font-family:'Bricolage Grotesque',sans-serif;font-size:15px;font-weight:700;padding:14px;background:#c62828;color:#ece8e0;border:0;border-radius:8px;cursor:pointer}
.err{font-family:'Fragment Mono',monospace;font-size:12px;color:#ef5350;margin-top:8px;min-height:18px}
</style>
</head>
<body>
<div class="gate">
<h1>Echobox</h1>
<p class="sub">Call Report</p>
<form method=POST action="/">
<input type=password name=password placeholder=Password autofocus>
<button>Enter</button>
<p class="err">WRONG_MSG</p>
</form>
</div>
</body>
</html>`;

module.exports = async function(req, res) {
    if (req.method === 'POST') {
        let b = '';
        let size = 0;
        const MAX_BODY = 1024;
        await new Promise(r => {
            req.on('data', c => { size += c.length; if (size <= MAX_BODY) b += c; });
            req.on('end', r);
        });
        if (size > MAX_BODY) { res.writeHead(413); res.end('Too large'); return; }
        const p = new URLSearchParams(b).get('password') || '';
        if (crypto.timingSafeEqual(Buffer.from(makeToken(p)), Buffer.from(VALID_TOKEN))) {
            res.setHeader('Set-Cookie',
                `${COOKIE_NAME}=${VALID_TOKEN}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${COOKIE_MAX_AGE}${req.headers['x-forwarded-proto'] === 'https' ? '; Secure' : ''}`);
            res.writeHead(303, { Location: '/' });
            res.end();
            return;
        }
        res.setHeader('Content-Type', 'text/html');
        res.end(LOGIN_PAGE.replace('WRONG_MSG', 'Wrong password.'));
        return;
    }

    const t = parseCookies(req.headers.cookie)[COOKIE_NAME] || '';
    if (t.length === VALID_TOKEN.length && crypto.timingSafeEqual(Buffer.from(t), Buffer.from(VALID_TOKEN))) {
        const h = fs.readFileSync(path.join(__dirname, '..', 'report.html'), 'utf-8');
        res.setHeader('Content-Type', 'text/html');
        res.setHeader('Cache-Control', 'private, no-cache');
        res.end(h);
        return;
    }

    res.setHeader('Content-Type', 'text/html');
    res.end(LOGIN_PAGE.replace('WRONG_MSG', ''));
};

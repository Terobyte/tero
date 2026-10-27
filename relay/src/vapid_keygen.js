// relay/src/vapid_keygen.js — VAPID-ключи (P-256) через WebCrypto.
// В stdlib Python P-256 нет, поэтому генерация здесь:
// `node relay/src/vapid_keygen.js`. Печатает JSON {public, private} (JWK)
// в stdout — секретов в аргументах и окружении нет.
const keyPair = await crypto.subtle.generateKey(
  { name: 'ECDSA', namedCurve: 'P-256' },
  true,
  ['sign', 'verify'],
);
const [pub, priv] = await Promise.all([
  crypto.subtle.exportKey('jwk', keyPair.publicKey),
  crypto.subtle.exportKey('jwk', keyPair.privateKey),
]);
console.log(JSON.stringify({ public: pub, private: priv }));

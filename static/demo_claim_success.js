const STORAGE_KEY = 'nimhunt-demo-spot-v1';
const COMPLETED_KEY = 'nimhunt-demo-completed-v1';

try {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.setItem(COMPLETED_KEY, '1');
} catch (_err) {}

function launchConfetti() {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const confetti = document.createElement('div');
    confetti.className = 'nh-confetti';
    confetti.setAttribute('aria-hidden', 'true');
    for (let index = 0; index < 34; index += 1) {
        const piece = document.createElement('span');
        const angle = ((Math.PI * 2) * index / 34) + ((Math.random() - 0.5) * 0.58);
        const distance = 24 + Math.random() * 36;
        piece.style.setProperty('--tx', `${Math.cos(angle) * distance}vmin`);
        piece.style.setProperty('--ty', `${Math.sin(angle) * distance}vmin`);
        piece.style.setProperty('--drift', `${(Math.random() - 0.5) * 12}vmin`);
        piece.style.setProperty('--delay', `${Math.random() * 70}ms`);
        piece.style.setProperty('--duration', `${780 + Math.random() * 360}ms`);
        piece.style.setProperty('--rotation', `${Math.round(Math.random() * 720 - 360)}deg`);
        piece.style.setProperty('--size', `${7 + Math.random() * 7}px`);
        piece.className = index % 3 === 0 ? 'is-gold' : (index % 3 === 1 ? 'is-green' : 'is-blue');
        confetti.append(piece);
    }
    document.body.append(confetti);
    window.setTimeout(() => confetti.remove(), 1250);
}

window.requestAnimationFrame(() => window.setTimeout(launchConfetti, 120));

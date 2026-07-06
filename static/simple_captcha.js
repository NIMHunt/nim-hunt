// Reusable small math-captcha helper for NimHunt modal forms.
// This is intentionally simple UI friction, not a replacement for server-side rate limiting.

function randomInt(min, max) {
    const lo = Math.ceil(Number(min));
    const hi = Math.floor(Number(max));
    return Math.floor(Math.random() * (hi - lo + 1)) + lo;
}

function parseAnswer(value) {
    const parsed = Number.parseInt(String(value || '').trim(), 10);
    return Number.isFinite(parsed) ? parsed : null;
}

export function createCaptchaController({
    questionEl,
    inputEl,
    questionText = ({ a, b }) => `What is ${a} + ${b}?`,
    min = 1,
    max = 9,
    onChange = null,
} = {}) {
    const state = {
        a: 0,
        b: 0,
    };

    function payload() {
        return {
            captcha_a: state.a,
            captcha_b: state.b,
            captcha_answer: parseAnswer(inputEl?.value) ?? 0,
        };
    }

    function passed() {
        const answer = parseAnswer(inputEl?.value);
        return answer !== null && answer === state.a + state.b;
    }

    function notify() {
        if (typeof onChange === 'function') onChange({ passed: passed(), payload: payload() });
    }

    function reset() {
        state.a = randomInt(min, max);
        state.b = randomInt(min, max);

        if (questionEl) {
            questionEl.textContent = questionText({ a: state.a, b: state.b });
        }
        if (inputEl) {
            inputEl.value = '';
        }

        notify();
    }

    function bind() {
        if (!inputEl) return;
        for (const eventName of ['input', 'change', 'keyup', 'paste']) {
            inputEl.addEventListener(eventName, () => {
                window.setTimeout(notify, eventName === 'paste' ? 0 : 0);
            });
        }
    }

    bind();
    reset();

    return {
        reset,
        passed,
        payload,
    };
}

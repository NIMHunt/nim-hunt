const faqItems = [...document.querySelectorAll('[data-faq-item]')];

function setExpanded(item, nextExpanded) {
    const toggle = item.querySelector('.faq-toggle');
    const answer = item.querySelector('.faq-answer');
    if (!toggle || !answer) return;

    const expanded = Boolean(nextExpanded);
    item.classList.toggle('is-expanded', expanded);
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    answer.hidden = !expanded;
}

function collapseOtherItems(activeItem) {
    for (const item of faqItems) {
        if (item !== activeItem) setExpanded(item, false);
    }
}

for (const item of faqItems) {
    const toggle = item.querySelector('.faq-toggle');
    if (!toggle) continue;

    toggle.addEventListener('click', () => {
        const expanding = toggle.getAttribute('aria-expanded') !== 'true';
        if (expanding) collapseOtherItems(item);
        setExpanded(item, expanding);
    });

    setExpanded(item, false);
}

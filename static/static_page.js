import { getStaticPageText } from './static_page_text.js?v=static-pages-v1-20260724';

const text = getStaticPageText();
const pageName = document.body.dataset.staticPage;
const titleElement = document.querySelector('[data-static-page-title]');
const contentElement = document.querySelector('[data-static-page-content]');
const heroLink = document.querySelector('.home-hero-link');
const heroScreenreaderText = document.querySelector('#connection-line');

function setSharedText(pageText) {
    document.title = pageText.pageTitle;
    titleElement.textContent = pageText.title;
    heroLink?.setAttribute('aria-label', text.common.returnHome);
    if (heroScreenreaderText) heroScreenreaderText.textContent = text.common.returnHome;
}

function makeParagraph(value) {
    const paragraph = document.createElement('p');
    paragraph.className = 'static-page-copy';
    paragraph.textContent = value;
    return paragraph;
}

function renderAbout() {
    setSharedText(text.about);
    contentElement.replaceChildren(...text.about.paragraphs.map(makeParagraph));
}

function validRoadmapSections(value) {
    if (!Array.isArray(value)) return [];

    return value
        .map((section) => ({
            heading: typeof section?.heading === 'string' ? section.heading.trim() : '',
            items: Array.isArray(section?.items)
                ? section.items
                    .filter((item) => typeof item === 'string' && item.trim())
                    .map((item) => item.trim())
                : [],
        }))
        .filter((section) => section.heading && section.items.length > 0);
}

function makeRoadmapSection(section) {
    const wrapper = document.createElement('section');
    wrapper.className = 'roadmap-section';

    const heading = document.createElement('h3');
    heading.className = 'nq-label roadmap-heading';
    heading.textContent = section.heading;

    const list = document.createElement('ul');
    list.className = 'roadmap-items';

    for (const itemText of section.items) {
        const item = document.createElement('li');
        item.className = 'static-page-copy roadmap-item';
        item.textContent = itemText;
        list.append(item);
    }

    wrapper.append(heading, list);
    return wrapper;
}

async function renderRoadmap() {
    setSharedText(text.roadmap);

    try {
        const response = await fetch('/static/roadmap.json', { cache: 'no-store' });
        if (!response.ok) throw new Error(`Roadmap request failed (${response.status})`);

        const data = await response.json();
        const sections = validRoadmapSections(data?.sections);
        if (sections.length === 0) {
            contentElement.replaceChildren(makeParagraph(text.roadmap.empty));
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'roadmap-sections';
        wrapper.append(...sections.map(makeRoadmapSection));
        contentElement.replaceChildren(wrapper);
    } catch (error) {
        console.error('Unable to load the NimHunt roadmap.', error);
        contentElement.replaceChildren(makeParagraph(text.roadmap.loadFailed));
    }
}

if (pageName === 'about') {
    renderAbout();
} else if (pageName === 'roadmap') {
    renderRoadmap();
} else {
    contentElement?.replaceChildren(makeParagraph(text.common.loadFailed));
}

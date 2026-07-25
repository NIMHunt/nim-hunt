import { getStaticPageText } from './static_page_text.js?v=about-nimpay-link-v1-20260725';

const text = getStaticPageText();
const pageName = document.body.dataset.homeInformationView;
const titleElement = document.querySelector('[data-static-page-title]');
const contentElement = document.querySelector('[data-static-page-content]');

function setSharedText(pageText) {
    document.title = pageText.pageTitle;
    if (titleElement) titleElement.textContent = pageText.title;
}

function appendParagraphPart(paragraph, part) {
    if (typeof part === 'string') {
        paragraph.append(document.createTextNode(part));
        return;
    }
    if (!part || typeof part.text !== 'string') return;

    if (typeof part.href === 'string' && part.href.trim()) {
        const link = document.createElement('a');
        link.href = part.href;
        link.className = 'welcome-link';
        link.textContent = part.text;
        paragraph.append(link);
        return;
    }

    paragraph.append(document.createTextNode(part.text));
}

function makeParagraph(value) {
    const paragraph = document.createElement('p');
    paragraph.className = 'static-page-copy';

    if (typeof value === 'string') {
        paragraph.textContent = value;
    } else if (Array.isArray(value?.parts)) {
        for (const part of value.parts) appendParagraphPart(paragraph, part);
    }

    return paragraph;
}

function renderAbout() {
    setSharedText(text.about);
    contentElement?.replaceChildren(...text.about.paragraphs.map(makeParagraph));
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
        item.className = 'nq-text roadmap-item';
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
            contentElement?.replaceChildren(makeParagraph(text.roadmap.empty));
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'roadmap-sections';
        wrapper.append(...sections.map(makeRoadmapSection));
        contentElement?.replaceChildren(wrapper);
    } catch (error) {
        console.error('Unable to load the NimHunt roadmap.', error);
        contentElement?.replaceChildren(makeParagraph(text.roadmap.loadFailed));
    }
}

if (pageName === 'about') {
    renderAbout();
} else if (pageName === 'roadmap') {
    renderRoadmap();
}

// Which layout to render: the desktop one, or the phone one with a tab bar,
// tappable rows and bottom sheets. The phone layout's media query is written
// once, on the <link> that loads mobile.css, so styles and scripts agree.

const link = document.querySelector('link[rel="stylesheet"][data-layout="mobile"]');
const media = window.matchMedia(link && link.media ? link.media : "not all");

export function isMobile() {
  return media.matches;
}

export function onLayoutChange(handler) {
  media.addEventListener("change", () => handler(media.matches));
}

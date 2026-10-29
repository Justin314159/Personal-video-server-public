// Tiny hash router: #/name/arg -> screen.render(root, arg)
const routes = {};
let current = null;

export function route(name, screen) { routes[name] = screen; }

export function navigate(hash, replace = false) {
  if (replace) location.replace(hash); else location.hash = hash;
}

let visited = 0;

export function back(fallback = '#/foryou') {
  if (visited > 1) history.back();
  else navigate(fallback, true);
}

export function currentRoute() {
  const [name, ...rest] = (location.hash.replace(/^#\/?/, '') || '').split('/');
  return { name: name || '', arg: rest.map(decodeURIComponent).join('/') };
}

// While a screen is still fetching and hasn't put anything on the page, show
// a soft placeholder instead of a blank screen (only if it takes a moment).
function placeholder(root) {
  const ph = document.createElement('div');
  ph.className = 'screen route-placeholder';
  ph.setAttribute('aria-busy', 'true');
  ph.innerHTML = '<div class="sk sk-title"></div><div class="skeleton grid">' +
    '<div class="sk-card"><div class="sk sk-thumb"></div><div class="sk sk-line"></div><div class="sk sk-line short"></div></div>'.repeat(3) + '</div>';
  const timer = setTimeout(() => { if (!root.childElementCount) root.append(ph); }, 150);
  const obs = new MutationObserver(() => {
    if ([...root.children].some((c) => c !== ph)) { ph.remove(); obs.disconnect(); }
  });
  obs.observe(root, { childList: true });
  return () => { clearTimeout(timer); obs.disconnect(); ph.remove(); };
}

let renderSeq = 0;

export async function render(root, guard) {
  const { name, arg } = currentRoute();
  const redirect = guard(name);
  if (redirect) { navigate(redirect, true); return; }
  const screen = routes[name] || routes['foryou'];
  if (current && current.unmount) {
    try { current.unmount(); } catch (e) { console.error(e); }
  }
  current = screen;
  visited += 1;
  const seq = ++renderSeq;
  root.replaceChildren();
  root.dataset.route = name || 'foryou';
  window.scrollTo(0, 0);
  const done = placeholder(root);
  try {
    await screen.render(root, arg);
  } catch (e) {
    console.error(e);
    if (seq === renderSeq) {
      const box = document.createElement('div');
      box.className = 'screen';
      box.innerHTML = '<div class="empty"><h3>That didn’t load</h3><p></p><button class="btn">Try again</button></div>';
      box.querySelector('p').textContent = e.code === 'offline' ? e.message : 'Something went wrong while opening this page.';
      box.querySelector('button').addEventListener('click', () => render(root, guard));
      root.append(box);
    }
  } finally {
    done();
  }
  return name;
}

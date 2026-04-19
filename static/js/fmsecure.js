/**
 * FMSecure — fmsecure.js
 * Base utilities: nav scroll, mobile drawer, scroll-reveal, FAQ, animations
 */

/* ── Navigation scroll behaviour ─────────────────────────────────────────── */
(function () {
  const nav = document.querySelector('.nav');
  if (!nav) return;

  const onScroll = () => {
    if (window.scrollY > 20) {
      nav.classList.add('scrolled');
    } else {
      nav.classList.remove('scrolled');
    }
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll(); // Run once on load
})();

/* ── Mobile nav hamburger ─────────────────────────────────────────────────── */
(function () {
  const hamburger = document.querySelector('.nav-hamburger');
  const drawer = document.querySelector('.nav-drawer');
  if (!hamburger || !drawer) return;

  hamburger.addEventListener('click', () => {
    drawer.classList.toggle('open');
    // Animate bars
    const bars = hamburger.querySelectorAll('span');
    if (drawer.classList.contains('open')) {
      bars[0].style.transform = 'rotate(45deg) translate(5px, 5px)';
      bars[1].style.opacity = '0';
      bars[2].style.transform = 'rotate(-45deg) translate(5px, -5px)';
    } else {
      bars.forEach(b => { b.style.transform = ''; b.style.opacity = ''; });
    }
  });

  // Close drawer on link click
  drawer.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      drawer.classList.remove('open');
      const bars = hamburger.querySelectorAll('span');
      bars.forEach(b => { b.style.transform = ''; b.style.opacity = ''; });
    });
  });
})();

/* ── Scroll-reveal observer ───────────────────────────────────────────────── */
(function () {
  const elements = document.querySelectorAll('.reveal');
  if (!elements.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed');
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
  );

  elements.forEach(el => observer.observe(el));
})();

/* ── Architecture layer reveal ────────────────────────────────────────────── */
(function () {
  const layers = document.querySelectorAll('.arch-layer');
  if (!layers.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const allLayers = entry.target.parentElement.querySelectorAll('.arch-layer');
          allLayers.forEach((layer, i) => {
            setTimeout(() => {
              layer.style.transition = `opacity 0.5s ease, transform 0.5s ease`;
              layer.style.opacity = '1';
              layer.style.transform = 'translateX(0)';
            }, i * 80);
          });
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1 }
  );

  if (layers[0]) observer.observe(layers[0]);
})();

/* ── Pricing card reveal ──────────────────────────────────────────────────── */
(function () {
  const cards = document.querySelectorAll('.price-card');
  if (!cards.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const allCards = entry.target.parentElement.querySelectorAll('.price-card');
          allCards.forEach((card, i) => {
            setTimeout(() => {
              card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
              card.style.opacity = '1';
              card.style.transform = 'translateY(0)';
            }, i * 100);
          });
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1 }
  );

  // Set initial state
  cards.forEach(c => { c.style.opacity = '0'; c.style.transform = 'translateY(20px)'; });
  if (cards[0]) observer.observe(cards[0]);
})();

/* ── FAQ accordion ────────────────────────────────────────────────────────── */
(function () {
  const items = document.querySelectorAll('.faq-item');
  items.forEach(item => {
    const btn = item.querySelector('.faq-question');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const wasOpen = item.classList.contains('open');
      // Close all
      items.forEach(i => i.classList.remove('open'));
      // Open clicked if it wasn't open
      if (!wasOpen) item.classList.add('open');
    });
  });
})();

/* ── Number counter animation ─────────────────────────────────────────────── */
function animateCounter(el, target, duration = 1500, prefix = '', suffix = '') {
  const start = performance.now();
  const startVal = 0;
  const isDecimal = target.toString().includes('.');

  const tick = (now) => {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    // Ease out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = startVal + (target - startVal) * eased;
    el.textContent = prefix + (isDecimal ? current.toFixed(1) : Math.round(current).toLocaleString()) + suffix;
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

(function () {
  const counters = document.querySelectorAll('[data-count]');
  if (!counters.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          const target = parseFloat(el.dataset.count);
          const prefix = el.dataset.prefix || '';
          const suffix = el.dataset.suffix || '';
          animateCounter(el, target, 1800, prefix, suffix);
          observer.unobserve(el);
        }
      });
    },
    { threshold: 0.5 }
  );

  counters.forEach(el => observer.observe(el));
})();

/* ── Terminal typing animation ────────────────────────────────────────────── */
function typeText(el, text, speed = 40, callback) {
  let i = 0;
  el.textContent = '';
  const tick = () => {
    if (i < text.length) {
      el.textContent += text[i++];
      setTimeout(tick, speed + Math.random() * 20);
    } else if (callback) {
      callback();
    }
  };
  tick();
}

(function () {
  const terminal = document.querySelector('.terminal-animate');
  if (!terminal) return;

  const lines = terminal.querySelectorAll('[data-type]');
  if (!lines.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          let delay = 300;
          lines.forEach(line => {
            setTimeout(() => {
              line.style.opacity = '1';
              const text = line.dataset.type;
              const speed = parseInt(line.dataset.speed) || 35;
              typeText(line, text, speed);
            }, delay);
            delay += (line.dataset.type.length * 35) + 200;
          });
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.3 }
  );

  lines.forEach(l => { l.style.opacity = '0'; l.textContent = ''; });
  observer.observe(terminal);
})();

/* ── Active nav link highlight ────────────────────────────────────────────── */
(function () {
  const path = window.location.pathname;
  document.querySelectorAll('.nav-links a, .nav-drawer a').forEach(a => {
    if (a.getAttribute('href') === path) {
      a.classList.add('active');
    }
  });
})();

/* ── Smooth scroll for anchor links ──────────────────────────────────────── */
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener('click', (e) => {
    const id = a.getAttribute('href').slice(1);
    const target = document.getElementById(id);
    if (target) {
      e.preventDefault();
      const navH = parseInt(getComputedStyle(document.documentElement)
        .getPropertyValue('--nav-h')) || 64;
      const top = target.getBoundingClientRect().top + window.scrollY - navH - 16;
      window.scrollTo({ top, behavior: 'smooth' });
    }
  });
});

/* ── Utility: show toast notification ────────────────────────────────────── */
function showToast(message, type = 'info', duration = 3500) {
  const colors = {
    info:    { bg: 'var(--blue-dim)', border: 'var(--blue)', text: '#93c5fd' },
    success: { bg: 'var(--green-dim)', border: 'var(--green)', text: '#6ee7b7' },
    error:   { bg: 'var(--red-dim)', border: 'var(--red)', text: '#fca5a5' },
    warning: { bg: 'var(--amber-dim)', border: 'var(--amber)', text: '#fcd34d' },
  };
  const c = colors[type] || colors.info;

  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed; bottom:24px; right:24px; z-index:9999;
    background:${c.bg}; border:1px solid ${c.border};
    border-radius:10px; padding:12px 18px;
    color:${c.text}; font-size:13.5px; font-weight:500;
    box-shadow:0 8px 32px rgba(0,0,0,0.5);
    transform:translateY(20px); opacity:0;
    transition:all 0.3s ease; max-width:340px;
    font-family:'DM Sans',sans-serif;
  `;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.style.transform = 'translateY(0)';
    toast.style.opacity = '1';
  });
  setTimeout(() => {
    toast.style.transform = 'translateY(20px)';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

window.FMSecure = { showToast, animateCounter, typeText };
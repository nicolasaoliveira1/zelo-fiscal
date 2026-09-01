const SELETOR_CONTROLES = [
  '.app-sidebar a.sidebar-marca',
  '.app-sidebar a.sidebar-link',
  '.app-sidebar .sidebar-pe button.sidebar-mini',
].join(', ');
const LIMITE_ROTULO_VISIVEL = 2;

function fonteDoControle(controle) {
  if (controle.matches('.sidebar-marca')) {
    return controle.querySelector('.sidebar-title');
  }
  if (controle.matches('.sidebar-link')) {
    return controle.querySelector('.sidebar-rotulo');
  }
  return null;
}

function textoDoControle(controle) {
  const fonte = fonteDoControle(controle);
  const textoDaFonte = fonte?.textContent?.trim();
  return textoDaFonte || controle.getAttribute('aria-label')?.trim() || '';
}

function rotuloEstaVisivel(controle) {
  const fonte = fonteDoControle(controle);
  return fonte?.getBoundingClientRect().width > LIMITE_ROTULO_VISIVEL;
}

function dicaPodeAparecer(controle) {
  if (controle.matches('.sidebar-pe .sidebar-mini')) {
    return true;
  }
  return !rotuloEstaVisivel(controle);
}

function inicializarDicas() {
  const bootstrap = globalThis.bootstrap;
  if (!bootstrap?.Tooltip) {
    return;
  }

  const dicas = [];
  document.querySelectorAll(SELETOR_CONTROLES).forEach((controle) => {
    if (!textoDoControle(controle)) {
      return;
    }

    const instancia = new bootstrap.Tooltip(controle, {
      title: () => textoDoControle(controle),
      container: 'body',
      placement: 'right',
      trigger: 'manual',
      customClass: 'sidebar-dica',
    });
    dicas.push({ controle, instancia });

    const esconder = () => instancia.hide();
    const mostrar = () => {
      if (dicaPodeAparecer(controle) && textoDoControle(controle)) {
        instancia.show();
      } else {
        esconder();
      }
    };

    controle.addEventListener('mouseenter', mostrar);
    controle.addEventListener('mouseleave', esconder);
    controle.addEventListener('focusin', mostrar);
    controle.addEventListener('focusout', esconder);
    controle.addEventListener('click', esconder);
  });

  const sincronizar = () => {
    dicas.forEach(({ controle, instancia }) => {
      if (!dicaPodeAparecer(controle)) {
        instancia.hide();
      }
    });
  };

  new MutationObserver(sincronizar).observe(document.body, {
    attributes: true,
    attributeFilter: ['class'],
  });
  window.addEventListener('resize', sincronizar);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', inicializarDicas, { once: true });
} else {
  inicializarDicas();
}

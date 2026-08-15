import { showToast } from './toasts.js';

// Expoe o toast para scripts nao-modulo (ex.: handler .btn-abrir-pasta no base.html),
// que ate entao caiam no alert() nativo por window.showToast estar indefinido.
window.showToast = showToast;

        document.addEventListener('DOMContentLoaded', function () {


            const tooltipTriggerList = Array.from(document.querySelectorAll('[data-bs-tooltip="true"]'));
            tooltipTriggerList.forEach((tooltipTriggerEl) => {
                new bootstrap.Tooltip(tooltipTriggerEl, { trigger: 'hover' });
            });

            document.addEventListener('click', function (event) {
                const tooltipTarget = event.target.closest('[data-bs-tooltip="true"]');
                if (!tooltipTarget) return;

                const tooltipInstance = bootstrap.Tooltip.getInstance(tooltipTarget);
                if (tooltipInstance) tooltipInstance.hide();
            });


            function appendRequestId(message, requestId) {
                if (!requestId) return message;
                return `${message} (req: ${requestId})`;
            }

            function buildErrorMessage(data, fallback) {
                let msg = (data && data.message) ? data.message : (fallback || 'Erro');
                // mensagem ja costuma vir como "Titulo: acao"; so acrescenta a acao se vier separada
                if (data && data.acao && msg.indexOf(data.acao) === -1) {
                    msg = `${msg} — ${data.acao}`;
                }
                const requestId = data && data.request_id ? data.request_id : '';
                return appendRequestId(msg, requestId);
            }

            async function copyToClipboard(text) {
                if (!text) return false;

                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                    return true;
                }

                const tempInput = document.createElement('input');
                tempInput.value = text;
                tempInput.style.position = 'fixed';
                tempInput.style.opacity = '0';
                document.body.appendChild(tempInput);
                tempInput.focus();
                tempInput.select();

                let copied = false;
                try {
                    copied = document.execCommand('copy');
                } catch (e) {
                    copied = false;
                }

                document.body.removeChild(tempInput);
                return copied;
            }

            function resetDownloadButton(button, originalHTML) {
                delete button.dataset.imbeTipoEscolhido;
                button.innerHTML = originalHTML;
                button.disabled = false;
            }

            document.addEventListener('click', async function (event) {
                const btn = event.target.closest('.copy-cnpj');
                if (!btn) return;

                const cnpj = (btn.dataset.cnpj || '').trim();
                if (!cnpj) {
                    showToast('CNPJ não disponível para cópia.', 'error');
                    return;
                }

                try {
                    const ok = await copyToClipboard(cnpj);
                    if (ok) {
                        showToast('CNPJ copiado para a área de transferência!', 'success');
                    } else {
                        showToast('Não foi possível copiar o CNPJ.', 'error');
                    }
                } catch (err) {
                    showToast('Erro ao copiar CNPJ.', 'error');
                }
            });

            const confirmModalElement = document.getElementById('confirmarDataModal');
            let confirmModal = confirmModalElement ? new bootstrap.Modal(confirmModalElement) : null;

            const pendenteModalElement = document.getElementById('marcarPendenteModal');
            let pendenteModal = pendenteModalElement ? new bootstrap.Modal(pendenteModalElement) : null;

            const estadualRsPositivaModalElement = document.getElementById('estadualRsPositivaModal');
            let estadualRsPositivaModal = estadualRsPositivaModalElement ? new bootstrap.Modal(estadualRsPositivaModalElement) : null;
            const displayTipoPositivaRs = document.getElementById('displayTipoPositivaRs');
            const displayEmpresaPositivaRs = document.getElementById('displayEmpresaPositivaRs');

            const infoModalElement = document.getElementById('infoModal');
            let infoModal = infoModalElement ? new bootstrap.Modal(infoModalElement) : null;
            const infoModalBody = document.getElementById('infoModalBody');
            const btnInfoModalOK = document.getElementById('btnInfoModalOK');

            const fgtsBatchModalElement = document.getElementById('fgtsBatchModal');
            let fgtsBatchModal = fgtsBatchModalElement ? new bootstrap.Modal(fgtsBatchModalElement) : null;
            const fgtsBatchSummaryModalElement = document.getElementById('fgtsBatchSummaryModal');
            let fgtsBatchSummaryModal = fgtsBatchSummaryModalElement
                ? new bootstrap.Modal(fgtsBatchSummaryModalElement, { backdrop: 'static', keyboard: false })
                : null;
            const fgtsBatchVencidas = document.getElementById('fgtsBatchVencidas');
            const fgtsBatchAVencer = document.getElementById('fgtsBatchAVencer');
            const fgtsBatchTotal = document.getElementById('fgtsBatchTotal');
            const fgtsBatchLabelVencidas = document.getElementById('fgtsBatchLabelVencidas');
            const fgtsBatchLabelAVencer = document.getElementById('fgtsBatchLabelAVencer');
            const fgtsBatchLabelTotal = document.getElementById('fgtsBatchLabelTotal');
            const fgtsBatchScopeHint = document.getElementById('fgtsBatchScopeHint');
            const fgtsBatchEmpresaDisplay = document.getElementById('fgtsBatchEmpresaDisplay');
            const btnFgtsBatchStart = document.getElementById('btnFgtsBatchStart');
            const btnFgtsSingleEmit = document.getElementById('btnFgtsSingleEmit');
            const fgtsBatchSummaryTotal = document.getElementById('fgtsBatchSummaryTotal');
            const fgtsBatchSummaryTempo = document.getElementById('fgtsBatchSummaryTempo');
            const fgtsBatchSummaryTaxa = document.getElementById('fgtsBatchSummaryTaxa');
            const fgtsBatchSummaryNotice = document.getElementById('fgtsBatchSummaryNotice');

            const fgtsBatchOverlay = document.getElementById('fgts-batch-overlay');
            const fgtsPainel = elementosPainelLote('fgts');
            const btnFgtsBatchPause = document.getElementById('btnFgtsBatchPause');
            const btnFgtsBatchResume = document.getElementById('btnFgtsBatchResume');
            const btnFgtsBatchStop = document.getElementById('btnFgtsBatchStop');

            const trabalhistaBatchModalElement = document.getElementById('trabalhistaBatchModal');
            let trabalhistaBatchModal = trabalhistaBatchModalElement ? new bootstrap.Modal(trabalhistaBatchModalElement) : null;
            const trabalhistaBatchSummaryModalElement = document.getElementById('trabalhistaBatchSummaryModal');
            let trabalhistaBatchSummaryModal = trabalhistaBatchSummaryModalElement
                ? new bootstrap.Modal(trabalhistaBatchSummaryModalElement, { backdrop: 'static', keyboard: false })
                : null;
            const trabalhistaBatchVencidas = document.getElementById('trabalhistaBatchVencidas');
            const trabalhistaBatchAVencer = document.getElementById('trabalhistaBatchAVencer');
            const trabalhistaBatchTotal = document.getElementById('trabalhistaBatchTotal');
            const trabalhistaBatchLabelVencidas = document.getElementById('trabalhistaBatchLabelVencidas');
            const trabalhistaBatchLabelAVencer = document.getElementById('trabalhistaBatchLabelAVencer');
            const trabalhistaBatchLabelTotal = document.getElementById('trabalhistaBatchLabelTotal');
            const trabalhistaBatchScopeHint = document.getElementById('trabalhistaBatchScopeHint');
            const trabalhistaBatchEmpresaDisplay = document.getElementById('trabalhistaBatchEmpresaDisplay');
            const btnTrabalhistaBatchStart = document.getElementById('btnTrabalhistaBatchStart');
            const btnTrabalhistaSingleEmit = document.getElementById('btnTrabalhistaSingleEmit');
            const trabalhistaBatchSummaryTotal = document.getElementById('trabalhistaBatchSummaryTotal');
            const trabalhistaBatchSummaryTempo = document.getElementById('trabalhistaBatchSummaryTempo');
            const trabalhistaBatchSummaryTaxa = document.getElementById('trabalhistaBatchSummaryTaxa');
            const trabalhistaBatchSummaryNotice = document.getElementById('trabalhistaBatchSummaryNotice');

            const trabalhistaBatchOverlay = document.getElementById('trabalhista-batch-overlay');
            const trabalhistaPainel = elementosPainelLote('trabalhista');
            const btnTrabalhistaBatchPause = document.getElementById('btnTrabalhistaBatchPause');
            const btnTrabalhistaBatchResume = document.getElementById('btnTrabalhistaBatchResume');
            const btnTrabalhistaBatchStop = document.getElementById('btnTrabalhistaBatchStop');

            const rsBatchModalElement = document.getElementById('rsBatchModal');
            let rsBatchModal = rsBatchModalElement ? new bootstrap.Modal(rsBatchModalElement) : null;
            const rsBatchSummaryModalElement = document.getElementById('rsBatchSummaryModal');
            let rsBatchSummaryModal = rsBatchSummaryModalElement
                ? new bootstrap.Modal(rsBatchSummaryModalElement, { backdrop: 'static', keyboard: false })
                : null;
            const rsBatchVencidas = document.getElementById('rsBatchVencidas');
            const rsBatchAVencer = document.getElementById('rsBatchAVencer');
            const rsBatchTotal = document.getElementById('rsBatchTotal');
            const rsBatchLabelVencidas = document.getElementById('rsBatchLabelVencidas');
            const rsBatchLabelAVencer = document.getElementById('rsBatchLabelAVencer');
            const rsBatchLabelTotal = document.getElementById('rsBatchLabelTotal');
            const rsBatchScopeHint = document.getElementById('rsBatchScopeHint');
            const rsBatchEmpresaDisplay = document.getElementById('rsBatchEmpresaDisplay');
            const btnRsBatchStart = document.getElementById('btnRsBatchStart');
            const btnRsSingleEmit = document.getElementById('btnRsSingleEmit');
            const rsBatchSummaryPositivas = document.getElementById('rsBatchSummaryPositivas');
            const rsBatchSummaryNegativas = document.getElementById('rsBatchSummaryNegativas');
            const rsBatchSummaryEfeitoNegativas = document.getElementById('rsBatchSummaryEfeitoNegativas');
            const rsBatchSummaryTotal = document.getElementById('rsBatchSummaryTotal');
            const rsBatchSummaryTempo = document.getElementById('rsBatchSummaryTempo');
            const rsBatchSummaryTaxa = document.getElementById('rsBatchSummaryTaxa');

            const rsBatchOverlay = document.getElementById('rs-batch-overlay');
            const rsPainel = elementosPainelLote('rs');
            const btnRsBatchPause = document.getElementById('btnRsBatchPause');
            const btnRsBatchResume = document.getElementById('btnRsBatchResume');
            const btnRsBatchStop = document.getElementById('btnRsBatchStop');

            const municipalBatchModalElement = document.getElementById('municipalBatchModal');
            let municipalBatchModal = municipalBatchModalElement ? new bootstrap.Modal(municipalBatchModalElement) : null;
            const municipalBatchSummaryModalElement = document.getElementById('municipalBatchSummaryModal');
            let municipalBatchSummaryModal = municipalBatchSummaryModalElement
                ? new bootstrap.Modal(municipalBatchSummaryModalElement, { backdrop: 'static', keyboard: false })
                : null;
            const municipalBatchVencidas = document.getElementById('municipalBatchVencidas');
            const municipalBatchAVencer = document.getElementById('municipalBatchAVencer');
            const municipalBatchTotal = document.getElementById('municipalBatchTotal');
            const municipalBatchLabelVencidas = document.getElementById('municipalBatchLabelVencidas');
            const municipalBatchLabelAVencer = document.getElementById('municipalBatchLabelAVencer');
            const municipalBatchLabelTotal = document.getElementById('municipalBatchLabelTotal');
            const municipalBatchScopeHint = document.getElementById('municipalBatchScopeHint');
            const municipalBatchEmpresaDisplay = document.getElementById('municipalBatchEmpresaDisplay');
            const btnMunicipalBatchStart = document.getElementById('btnMunicipalBatchStart');
            const btnMunicipalSingleEmit = document.getElementById('btnMunicipalSingleEmit');
            const municipalBatchSummaryTotal = document.getElementById('municipalBatchSummaryTotal');
            const municipalBatchSummaryTempo = document.getElementById('municipalBatchSummaryTempo');
            const municipalBatchSummaryTaxa = document.getElementById('municipalBatchSummaryTaxa');
            const municipalBatchSummaryNotice = document.getElementById('municipalBatchSummaryNotice');

            const municipalBatchOverlay = document.getElementById('municipal-batch-overlay');
            const municipalPainel = elementosPainelLote('municipal');
            const btnMunicipalBatchPause = document.getElementById('btnMunicipalBatchPause');
            const btnMunicipalBatchResume = document.getElementById('btnMunicipalBatchResume');
            const btnMunicipalBatchStop = document.getElementById('btnMunicipalBatchStop');

            const editModalEl = document.getElementById('editModal');
            let editModal = editModalEl ? new bootstrap.Modal(editModalEl) : null;

            const displayData = document.getElementById('displayDataEncontrada');
            const displayTipo = document.getElementById('displayTipoCertidao');
            const displayArquivoInfo = document.getElementById('displayArquivoInfo');
            const displayEmpresaInfo = document.getElementById('displayEmpresaInfo');
            const displayTipoPendente = document.getElementById('displayTipoPendente');
            const displayEmpresaPendente = document.getElementById('displayEmpresaPendente');
            const btnSalvar = document.getElementById('btnConfirmarSalvar');
            const btnConfirmarPendente = document.getElementById('btnConfirmarPendente');
            const btnVisualizarPdf = document.getElementById('btnVisualizarPdf');


            const btnsEditar = document.querySelectorAll('.btn-outline-warning');
            const btnPendenteManual = document.getElementById('btnPendenteManual');
            const btnSalvarManual = document.getElementById('btnSalvarManual');
            const editFormInput = document.getElementById('nova_validade');


            let dadosParaSalvar = {};
            let linhaAtualTabela = null;
            let certidaoIdPendente = null;
            let certidaoIdManual = null;


            let urlParaAbrir = null;
            let tipoParaAbrir = null;
            let cnpjParaAbrir = null;
            let empresaNomeParaAbrir = null;
            let idParaMonitorar = null;
            let fgtsBatchCertidaoId = null;
            let fgtsBatchPoller = null;
            let fgtsBatchLastCompletedId = null;
            let fgtsBatchSingleUrl = null;
            let fgtsBatchEmpresaNome = null;
            let fgtsBatchTipoCert = null;
            let fgtsBatchScope = 'default';
            let trabalhistaBatchCertidaoId = null;
            let trabalhistaBatchPoller = null;
            let trabalhistaBatchLastCompletedId = null;
            let trabalhistaBatchSingleUrl = null;
            let trabalhistaBatchEmpresaNome = null;
            let trabalhistaBatchTipoCert = null;
            let trabalhistaBatchScope = 'default';
            let rsBatchCertidaoId = null;
            let rsBatchPoller = null;
            let rsBatchLastCompletedId = null;
            let rsBatchSingleUrl = null;
            let rsBatchEmpresaNome = null;
            let rsBatchTipoCert = null;
            let rsBatchScope = 'default';
            let municipalBatchCertidaoId = null;
            let municipalBatchPoller = null;
            let municipalBatchLastCompletedId = null;
            let municipalBatchSingleUrl = null;
            let municipalBatchEmpresaNome = null;
            let municipalBatchTipoCert = null;
            let municipalBatchScope = 'default';
            let caminhoArquivoParaModal = null;
            let federalMonitorAtivo = false;
            let federalPendingConfirmation = false;
            let federalTabRef = null;
            let federalFinalize = null;
            let federalMonitorController = null;
            const loadingOverlay = document.getElementById('loading-overlay');
            const loadingDetalhes = document.getElementById('loading-detalhes');
            const loadingTitulo = document.getElementById('loadingTitulo');
            const loadingElapsed = document.getElementById('loadingElapsed');
            const loadingCnpj = document.getElementById('loadingCnpj');
            let loadingTimer = null;
            const statusesComModal = new Set([
                'success_file_saved',
                'window_closed_no_file',
                'estadual_rs_positiva',
                'municipal_pdf_positiva',
                'certidao_pdf_positiva'
            ]);

            function bindStaticModalShake(modalElement) {
                if (!modalElement) return;

                modalElement.addEventListener('hidePrevented.bs.modal', function () {
                    const dialog = modalElement.querySelector('.modal-dialog');
                    if (!dialog) return;

                    dialog.classList.remove('modal-shake');
                    // Forca reflow para reiniciar animacao em cliques consecutivos no backdrop.
                    void dialog.offsetWidth;
                    dialog.classList.add('modal-shake');

                    const removeShake = () => dialog.classList.remove('modal-shake');
                    dialog.addEventListener('animationend', removeShake, { once: true });
                });
            }

            bindStaticModalShake(fgtsBatchSummaryModalElement);
            bindStaticModalShake(trabalhistaBatchSummaryModalElement);
            bindStaticModalShake(rsBatchSummaryModalElement);
            bindStaticModalShake(municipalBatchSummaryModalElement);

            // "FGTS" e "CNDT" são siglas e ficam em caixa alta; "MUNICIPAL" e
            // "TRABALHISTA" gritariam no título, então viram Capitalizado.
            function rotuloTipoCertidao(tipo) {
                const t = (tipo || '').toString().trim();
                if (!t) return '';
                if (t.length <= 5) return t.toUpperCase();
                return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
            }

            // O CNPJ não chega por parâmetro, mas o card da empresa está na
            // página e traz o valor já formatado no botão de copiar. Best-effort:
            // sem card (ou com nome que não casa), a linha simplesmente não aparece.
            function cnpjDaEmpresaPorNome(nomeEmpresa) {
                if (!nomeEmpresa) return '';
                const cards = document.querySelectorAll('.company-card');
                for (const card of cards) {
                    if (card.dataset.nomeEmpresa !== nomeEmpresa) continue;
                    const botao = card.querySelector('.copy-cnpj');
                    return botao ? botao.textContent.trim() : '';
                }
                return '';
            }

            function showLoading(nomeEmpresa = '', tipoCertidao = '') {
                if (loadingOverlay) loadingOverlay.classList.remove('d-none');

                if (loadingTitulo) {
                    const rotulo = rotuloTipoCertidao(tipoCertidao);
                    loadingTitulo.textContent = rotulo ? `Certidão ${rotulo}` : 'Executando automação';
                }

                if (loadingDetalhes) {
                    if (nomeEmpresa) {
                        loadingDetalhes.innerHTML = '';
                        loadingDetalhes.append('Emitindo ');
                        const forte = document.createElement('strong');
                        forte.textContent = nomeEmpresa;
                        loadingDetalhes.append(forte);
                        loadingDetalhes.title = nomeEmpresa;
                    } else {
                        loadingDetalhes.textContent = 'Executando automação, aguarde…';
                        loadingDetalhes.title = '';
                    }
                }

                if (loadingCnpj) {
                    const cnpj = cnpjDaEmpresaPorNome(nomeEmpresa);
                    loadingCnpj.textContent = cnpj;
                    loadingCnpj.classList.toggle('d-none', !cnpj);
                }

                // Relógio local: aqui não há estado no servidor para consultar,
                // e é ele (com o ponto pulsante) que diz que a automação está viva.
                if (loadingElapsed) {
                    const inicio = Date.now();
                    loadingElapsed.textContent = '00:00';
                    if (loadingTimer) clearInterval(loadingTimer);
                    loadingTimer = setInterval(() => {
                        loadingElapsed.textContent = formatarRelogio(
                            Math.floor((Date.now() - inicio) / 1000));
                    }, 1000);
                }
            }

            function hideLoading() {
                if (loadingOverlay) loadingOverlay.classList.add('d-none');
                // sem isso o timer segue rodando por toda a vida da página, e a
                // próxima emissão abriria com dois intervalos escrevendo no mesmo
                // elemento.
                if (loadingTimer) {
                    clearInterval(loadingTimer);
                    loadingTimer = null;
                }
            }

            function normalizeBatchScope(scopeValue) {
                const value = (scopeValue || '').toString().trim().toLowerCase();
                return (value === 'pendente' || value === 'pendentes') ? 'pendentes' : 'default';
            }

            function isPendenteStatus(statusEspecial) {
                return (statusEspecial || '').toString().trim().toLowerCase() === 'pendente';
            }

            function applyBatchModalData(config, data, scope) {
                const scopeNorm = normalizeBatchScope(scope || data.scope);
                const pendentes = Number(data.pendentes || 0);
                const vencidas = Number(data.vencidas || 0);
                const aVencer = Number(data.a_vencer || 0);
                const total = Number(data.total || 0);
                const excluidasSituacao = Number(data.excluidas_situacao || 0);

                if (config.scopeHintEl) {
                    const avisos = [];
                    if (scopeNorm === 'pendentes') {
                        avisos.push('Modo pendentes: você pode emitir apenas esta empresa ou iniciar o lote com todas as pendentes deste tipo.');
                    }
                    // Sem esta linha o operador veria um total menor que o esperado
                    // e não teria como saber por quê (spec 08, DATA-02.9).
                    if (excluidasSituacao > 0) {
                        avisos.push(`${excluidasSituacao} ${excluidasSituacao === 1 ? 'certidão ficou' : 'certidões ficaram'} de fora: a empresa não consta como ativa na Receita.`);
                    }
                    if (avisos.length) {
                        config.scopeHintEl.textContent = avisos.join(' ');
                        config.scopeHintEl.classList.remove('d-none');
                    } else {
                        config.scopeHintEl.textContent = '';
                        config.scopeHintEl.classList.add('d-none');
                    }
                }

                if (scopeNorm === 'pendentes') {
                    if (config.labelVencidasEl) config.labelVencidasEl.textContent = 'Pendentes';
                    if (config.labelAVencerEl) config.labelAVencerEl.textContent = 'Outras situações';
                    if (config.valueVencidasEl) config.valueVencidasEl.textContent = pendentes;
                    if (config.valueAVencerEl) config.valueAVencerEl.textContent = Math.max(0, total - pendentes);
                } else {
                    if (config.labelVencidasEl) config.labelVencidasEl.textContent = 'Vencidas';
                    if (config.labelAVencerEl) config.labelAVencerEl.textContent = 'A vencer';
                    if (config.valueVencidasEl) config.valueVencidasEl.textContent = vencidas;
                    if (config.valueAVencerEl) config.valueAVencerEl.textContent = aVencer;
                }

                if (config.labelTotalEl) config.labelTotalEl.textContent = 'Total do lote';
                if (config.valueTotalEl) config.valueTotalEl.textContent = total;
            }

            function cleanupUiLocks(options = {}) {
                const keepLoading = options.keepLoading === true;
                const keepBatchOverlays = options.keepBatchOverlays === true;
                if (!keepLoading) {
                    hideLoading();
                }
                if (!keepBatchOverlays) {
                    if (fgtsBatchOverlay) fgtsBatchOverlay.classList.add('d-none');
                    if (rsBatchOverlay) rsBatchOverlay.classList.add('d-none');
                    if (municipalBatchOverlay) municipalBatchOverlay.classList.add('d-none');
                }

                const modalAbertoOuAbrindo = document.querySelector(
                    '.modal.show, .modal[aria-modal="true"], .modal[style*="display: block"]'
                );

                if (!modalAbertoOuAbrindo) {
                    document.querySelectorAll('.modal-backdrop').forEach(backdrop => backdrop.remove());
                    document.body.classList.remove('modal-open');
                    document.body.style.removeProperty('padding-right');
                    document.body.style.removeProperty('overflow');
                }
            }

            function ensureModalBackdrop() {
                const hasBackdrop = document.querySelector('.modal-backdrop');
                if (!hasBackdrop) {
                    const backdrop = document.createElement('div');
                    backdrop.className = 'modal-backdrop fade show';
                    document.body.appendChild(backdrop);
                }
                document.body.classList.add('modal-open');
                document.body.style.overflow = 'hidden';
            }

            function atualizarInfoArquivoModal(mensagemArquivo) {
                if (!displayArquivoInfo) return;

                const msg = (mensagemArquivo || '').toString().trim();
                const caminho = msg.replace(/^Arquivo\s+salvo\s+no\s+servidor:\s*/i, '')
                    .replace(/^Arquivo\s+salvo\s+em:\s*/i, '')
                    .trim();

                if (caminho) {
                    displayArquivoInfo.innerHTML = `PDF salvo na pasta correta.<br>Destino: <code>${caminho}</code>`;
                    displayArquivoInfo.style.display = 'block';
                } else {
                    displayArquivoInfo.textContent = 'PDF salvo na pasta correta.';
                    displayArquivoInfo.style.display = 'block';
                }

                if (displayEmpresaInfo) {
                    let empresaNome = '';
                    if (linhaAtualTabela) {
                        const card = linhaAtualTabela.closest('.company-card');
                        if (card && card.dataset.nomeEmpresa) {
                            empresaNome = card.dataset.nomeEmpresa;
                        }
                    }

                    if (empresaNome) {
                        displayEmpresaInfo.textContent = `Empresa: ${empresaNome}`;
                        displayEmpresaInfo.style.display = 'block';
                    } else {
                        displayEmpresaInfo.textContent = '';
                        displayEmpresaInfo.style.display = 'none';
                    }
                }
            }

            function limparInfoArquivoModal() {
                if (displayArquivoInfo) {
                    displayArquivoInfo.textContent = '';
                    displayArquivoInfo.style.display = 'none';
                }
                if (displayEmpresaInfo) {
                    displayEmpresaInfo.textContent = '';
                    displayEmpresaInfo.style.display = 'none';
                }
                if (btnVisualizarPdf) {
                    btnVisualizarPdf.removeAttribute('href');
                    btnVisualizarPdf.removeAttribute('data-url');
                    btnVisualizarPdf.classList.add('d-none');
                }
            }

            function atualizarBotaoVisualizar(token) {
                if (!btnVisualizarPdf) return;
                if (token) {
                    const url = `/certidao/visualizar/${token}`;
                    btnVisualizarPdf.setAttribute('data-url', url);
                    btnVisualizarPdf.classList.remove('d-none');
                } else {
                    btnVisualizarPdf.removeAttribute('href');
                    btnVisualizarPdf.removeAttribute('data-url');
                    btnVisualizarPdf.classList.add('d-none');
                }
            }

            function atualizarInfoPendenteModal(tipoCertidao) {
                if (displayTipoPendente) {
                    displayTipoPendente.textContent = tipoCertidao || '---';
                }

                if (!displayEmpresaPendente) return;

                let empresaNome = '';
                if (linhaAtualTabela) {
                    const card = linhaAtualTabela.closest('.company-card');
                    if (card && card.dataset.nomeEmpresa) {
                        empresaNome = card.dataset.nomeEmpresa;
                    }
                }

                displayEmpresaPendente.textContent = empresaNome || '---';
            }


            function atualizarLinhaUI(linha, classeStatusAntiga, novoTexto) {
                const span = linha.querySelector('[class*="cert-status-"]');
                if (!span) return;

                const isPendente = classeStatusAntiga === 'status-vermelho' &&
                    novoTexto && novoTexto.toLowerCase().includes('pendente');

                let cls, icone, statusCert, bold;

                if (isPendente) {
                    cls = 'cert-status-danger'; icone = 'bi-exclamation-circle-fill'; statusCert = 'pendentes'; bold = true;
                } else if (classeStatusAntiga === 'status-verde') {
                    cls = 'cert-status-ok'; icone = 'bi-check-circle-fill'; statusCert = 'validas'; bold = false;
                } else if (classeStatusAntiga === 'status-amarelo') {
                    cls = 'cert-status-warn'; icone = 'bi-exclamation-triangle-fill'; statusCert = 'a_vencer'; bold = true;
                } else if (classeStatusAntiga === 'status-vermelho') {
                    cls = 'cert-status-danger'; icone = 'bi-x-circle-fill'; statusCert = 'vencidas'; bold = true;
                } else {
                    cls = 'cert-status-muted'; icone = 'bi-question-circle'; statusCert = 'nao_definida'; bold = false;
                }

                span.className = `${cls}${bold ? ' fw-semibold' : ''} small d-inline-flex align-items-center gap-1`;
                span.innerHTML = `<i class="bi ${icone}"></i>${novoTexto}`;
                linha.dataset.statusCert = statusCert;
                atualizarContagensChips();
                // Toda mudanca de status passa por aqui — atualiza o (N) no title da aba.
                if (typeof window.atualizarPendencias === 'function') {
                    window.atualizarPendencias();
                }
            }

            function handleDownloadResponse(data) {
                const manterLoading = data && statusesComModal.has(data.status);
                cleanupUiLocks({ keepLoading: manterLoading });

                // recusa por automação em curso: além do toast, o dock pisca —
                // com o painel minimizado, o operador não tem outra pista de
                // qual lote está segurando o navegador.
                if (data && data.motivo === 'automacao_em_curso') sinalizarRecusaNoDock();

                if (data.status === 'success_file_saved') {
                    if (displayData) displayData.textContent = data.data_formatada;
                    if (displayTipo) displayTipo.textContent = data.tipo_certidao;
                    atualizarInfoArquivoModal(data.mensagem_arquivo);
                    atualizarBotaoVisualizar(data.visualizar_token);
                    dadosParaSalvar = { certidao_id: data.certidao_id, nova_validade: data.nova_data };
                    if (confirmModal) {
                        confirmModal.show();
                    } else {
                        hideLoading();
                    }
                    return;
                }

                if (data.status === 'window_closed_no_file') {
                    if (federalPendingConfirmation ||
                        (confirmModalElement && confirmModalElement.classList.contains('show'))) {
                        hideLoading();
                        return;
                    }
                    limparInfoArquivoModal();
                    atualizarInfoPendenteModal(data.tipo_certidao);
                    certidaoIdPendente = data.certidao_id;
                    if (pendenteModal) {
                        pendenteModal.show();
                    } else {
                        hideLoading();
                    }
                    return;
                }
                
                if (
                    data.status === 'estadual_rs_positiva'
                    || data.status === 'municipal_pdf_positiva'
                    || data.status === 'certidao_pdf_positiva'
                ) {
                    limparInfoArquivoModal();
                    if (displayTipoPositivaRs) displayTipoPositivaRs.textContent = data.tipo_certidao || 'CERTIDÃO';
                    if (displayEmpresaPositivaRs) {
                        const card = linhaAtualTabela ? linhaAtualTabela.closest('.company-card') : null;
                        displayEmpresaPositivaRs.textContent = card && card.dataset.nomeEmpresa ? card.dataset.nomeEmpresa : '';
                    }

                    if (linhaAtualTabela) {
                        atualizarLinhaUI(linhaAtualTabela, 'status-vermelho', 'PENDENTE');
                    }

                    if (estadualRsPositivaModal) {
                        estadualRsPositivaModal.show();
                    } else {
                        showToast(buildErrorMessage(
                            data,
                            'Certidão POSITIVA detectada. Marcada como pendente.'
                        ), 'error');
                    }
                    return;
                }

                if (data.status === 'success_file_saved_no_date') {
                    limparInfoArquivoModal();
                    showToast("Arquivo salvo! Sem regra de data.", "primary");
                    setTimeout(() => location.reload(), 2000);
                    return;
                }

                limparInfoArquivoModal();
                showToast(
                    appendRequestId(
                        'Aviso: ' + (data.message || 'Erro desconhecido'),
                        data.request_id
                    ),
                    'error'
                );
            }

            // BOTAO ABRIR SITE
            const btnsAbrirSite = document.querySelectorAll('.btn-abrir-site');
            btnsAbrirSite.forEach(function (btn) {
                btn.addEventListener('click', function (event) {
                    event.preventDefault();
                    linhaAtualTabela = btn.closest('tr');

                    urlParaAbrir = (btn.dataset.url || '').trim();
                    tipoParaAbrir = (btn.dataset.tipo || '').trim();
                    idParaMonitorar = (btn.dataset.id || '').trim();
                    cnpjParaAbrir = btn.dataset.cnpj;

                    // mesmo padrao do botao de download: nome da empresa no overlay
                    empresaNomeParaAbrir = '';
                    const cardAbrirSite = btn.closest('.company-card');
                    if (cardAbrirSite && cardAbrirSite.dataset.nomeEmpresa) {
                        empresaNomeParaAbrir = cardAbrirSite.dataset.nomeEmpresa;
                    }

                    if (!urlParaAbrir || urlParaAbrir === '#' || urlParaAbrir === 'undefined' || urlParaAbrir === 'null' || urlParaAbrir === 'none') {
                        showToast('URL não cadastrada.', 'error');
                        return;
                    }

                    infoModalBody.innerHTML = `
                    <p class="mb-2">Ao abrir o site, o CNPJ <strong>${cnpjParaAbrir}</strong> será copiado para você colar na página.</p>
                    <p class="text-muted small">Pressione <strong>OK</strong> para abrir o site e copiar o CNPJ.</p>
                `;
                    if (infoModal) infoModal.show();
                });
            });

            if (btnInfoModalOK) {
                btnInfoModalOK.addEventListener('click', function () {
                    if (infoModal) infoModal.hide();
                    if (!urlParaAbrir || urlParaAbrir === '#' || urlParaAbrir === 'undefined' || urlParaAbrir === 'null' || urlParaAbrir === 'none') {
                        showToast('URL não cadastrada.', 'error');
                        hideLoading();
                        return;
                    }
                    if (!/^https?:/i.test(urlParaAbrir)) {
                        showToast('URL inválida para abrir o site.', 'error');
                        hideLoading();
                        return;
                    }
                    if (!idParaMonitorar) {
                        showToast('Certidão inválida para monitoramento.', 'error');
                        hideLoading();
                        return;
                    }
                    
                    if (cnpjParaAbrir) {
                        navigator.clipboard.writeText(cnpjParaAbrir).then(function () {
                            showToast('CNPJ copiado para a área de transferência!', 'success');
                        }).catch(function (err) {
                            console.error('Erro copiar:', err);
                            showToast('Não foi possível copiar o CNPJ. Copie manualmente.', 'error');
                        });
                    }

                    let novaAba = null;
                    if (urlParaAbrir) {
                        novaAba = window.open(urlParaAbrir, '_blank');

                        if (!novaAba && tipoParaAbrir !== 'FEDERAL') {
                            showToast('Não foi possível abrir o site. Verifique se o bloqueador de pop-up está ativo.', 'error');
                            return;
                        }

                        if (tipoParaAbrir === 'FEDERAL') {
                            if (federalMonitorAtivo || federalPendingConfirmation) {
                                showToast('Monitoramento federal já está em andamento. Aguarde ou feche a aba da Receita Federal.', 'warning');
                                hideLoading();
                                return;
                            }
                            showLoading(empresaNomeParaAbrir || '', tipoParaAbrir || '');
                            let arquivoSalvo = false;
                            let federalFinalizado = false;
                            let federalTimer = null;
                            let federalFocusHandler = null;
                            const monitorController = new AbortController();
                            caminhoArquivoParaModal = null;
                            federalMonitorAtivo = true;
                            federalTabRef = novaAba || null;
                            federalMonitorController = monitorController;

                            const finalizarFederal = () => {
                                if (federalFinalizado) return;
                                federalFinalizado = true;
                                if (federalTimer) {
                                    clearInterval(federalTimer);
                                }

                                if (federalFocusHandler) {
                                    window.removeEventListener('focus', federalFocusHandler);
                                    federalFocusHandler = null;
                                }

                                const abaParaFechar = federalTabRef || novaAba || null;

                                federalMonitorAtivo = false;
                                federalFinalize = null;
                                try {
                                    if (monitorController) monitorController.abort();
                                } catch (e) {
                                    /* ignore */
                                }
                                federalMonitorController = null;

                                if (arquivoSalvo) {
                                    if (abaParaFechar) {
                                        try {
                                            if (!abaParaFechar.closed) {
                                                abaParaFechar.close();
                                                try { window.focus(); } catch (e) { /* ignore */ }
                                            }
                                        } catch (e) {
                                            // ignore close errors
                                        }
                                    }
                                    federalTabRef = null;

                                    let dataBanco = null;
                                    let dataFormatada = null;

                                    if (caminhoArquivoParaModal && caminhoArquivoParaModal.data_validade) {
                                        dataBanco = caminhoArquivoParaModal.data_validade;
                                        dataFormatada = caminhoArquivoParaModal.data_validade_formatada || null;
                                    } else {
                                        const hoje = new Date();
                                        const validade = new Date(hoje);
                                        validade.setDate(hoje.getDate() + 180);
                                        dataBanco = validade.toISOString().split('T')[0];
                                        dataFormatada = `${validade.getDate().toString().padStart(2, '0')}/${(validade.getMonth() + 1).toString().padStart(2, '0')}/${validade.getFullYear()}`;
                                    }

                                    if (displayData && dataFormatada) displayData.textContent = dataFormatada;
                                    if (displayTipo) displayTipo.textContent = "FEDERAL";
                                    atualizarInfoArquivoModal(caminhoArquivoParaModal && caminhoArquivoParaModal.mensagem ? caminhoArquivoParaModal.mensagem : caminhoArquivoParaModal);
                                    atualizarBotaoVisualizar(caminhoArquivoParaModal && caminhoArquivoParaModal.visualizar_token);
                                    dadosParaSalvar = { certidao_id: idParaMonitorar, nova_validade: dataBanco };
                                    federalPendingConfirmation = true;
                                    if (pendenteModal) pendenteModal.hide();
                                    if (confirmModal) {
                                        confirmModal.show();
                                        ensureModalBackdrop();
                                    } else {
                                        hideLoading();
                                    }
                                    return;
                                }

                                // Se uma confirmação de download bem-sucedido já está pendente,
                                // não sobrescrever com o modal de pendente.
                                if (federalPendingConfirmation ||
                                    (confirmModalElement && confirmModalElement.classList.contains('show'))) {
                                    hideLoading();
                                    return;
                                }

                                federalTabRef = null;
                                atualizarInfoPendenteModal('FEDERAL');
                                certidaoIdPendente = idParaMonitorar;
                                if (confirmModal) confirmModal.hide();
                                if (pendenteModal) {
                                    pendenteModal.show();
                                    ensureModalBackdrop();
                                } else {
                                    hideLoading();
                                }
                            };

                            fetch(`/certidao/monitorar_download_federal/${idParaMonitorar}`, {
                                signal: monitorController.signal
                            })
                                .then(response => response.json())
                                .then(data => {
                                    if (data.status === 'success') {
                                        arquivoSalvo = true;
                                        caminhoArquivoParaModal = data;
                                        showToast("Arquivo salvo no servidor!", "success");
                                        if (novaAba) {
                                                try {
                                                    if (!novaAba.closed) novaAba.close();
                                                } catch (e) {
                                                    // ignore close errors
                                                }
                                            }
                                        finalizarFederal();
                                        return;
                                    }

                                    // COV-01b: Federal positiva detectada -> backend ja marcou
                                    // PENDENTE e removeu o PDF; sem modal de confirmacao de data.
                                    if (data.status === 'pendente') {
                                        showToast(
                                            appendRequestId(
                                                data.mensagem || data.message
                                                || 'Certidão Federal positiva: marcada como pendente.',
                                                data.request_id
                                            ),
                                            'warning'
                                        );
                                        if (novaAba) {
                                            try {
                                                if (!novaAba.closed) novaAba.close();
                                            } catch (e) {
                                                // ignore close errors
                                            }
                                        }
                                        // Reusa a limpeza completa do finalizarFederal (aborta
                                        // controller, limpa timer/focus handler); a guarda
                                        // federalPendingConfirmation evita o modal de pendente.
                                        federalPendingConfirmation = true;
                                        finalizarFederal();
                                        setTimeout(function () { window.location.reload(); }, 1200);
                                        return;
                                    }

                                    showToast(
                                        appendRequestId(
                                            data.message || data.mensagem || 'Monitoramento federal não encontrou arquivo.',
                                            data.request_id
                                        ),
                                        'error'
                                    );
                                    finalizarFederal();
                                })
                                .catch((err) => {
                                    if (err && err.name === 'AbortError') {
                                        return;
                                    }
                                    showToast('Erro ao monitorar download federal.', 'error');
                                    finalizarFederal();
                                });

                            federalFinalize = finalizarFederal;

                            if (novaAba) {
                                federalTimer = setInterval(function () {
                                    let fechado = false;
                                    try {
                                        fechado = novaAba.closed;
                                    } catch (e) {
                                        fechado = true;
                                    }

                                    if (fechado) {
                                        fetch('/certidao/monitorar_download_federal/stop', { method: 'POST' })
                                            .catch(() => null);
                                        monitorController.abort();
                                        finalizarFederal();
                                    }
                                }, 1000);
                            }

                            federalFocusHandler = function () {
                                if (!federalMonitorAtivo) return;

                                let fechado = false;
                                try {
                                    fechado = federalTabRef ? federalTabRef.closed : true;
                                } catch (e) {
                                    fechado = true;
                                }

                                if (fechado) {
                                    fetch('/certidao/monitorar_download_federal/stop', { method: 'POST' })
                                        .catch(() => null);
                                    if (federalMonitorController) {
                                        federalMonitorController.abort();
                                    }
                                    if (federalFinalize) {
                                        federalFinalize();
                                    }
                                }
                            };
                            window.addEventListener('focus', federalFocusHandler);
                        }
                    }
                });
            }

            // COV-01b: registrar Federal por upload manual (sem reabrir o portal).
            const registrarFederalModalEl = document.getElementById('registrarFederalModal');
            const registrarFederalModal = registrarFederalModalEl ? new bootstrap.Modal(registrarFederalModalEl) : null;
            const registrarFederalInput = document.getElementById('registrarFederalInput');
            const registrarFederalEmpresa = document.getElementById('registrarFederalEmpresa');
            const btnRegistrarFederalEnviar = document.getElementById('btnRegistrarFederalEnviar');
            let registrarFederalId = null;

            document.querySelectorAll('.btn-registrar-federal').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    registrarFederalId = btn.dataset.id;
                    if (registrarFederalEmpresa) registrarFederalEmpresa.textContent = btn.dataset.empresa || '';
                    if (registrarFederalInput) registrarFederalInput.value = '';
                    if (registrarFederalModal) registrarFederalModal.show();
                });
            });

            if (btnRegistrarFederalEnviar) {
                btnRegistrarFederalEnviar.addEventListener('click', function () {
                    const arquivo = registrarFederalInput && registrarFederalInput.files
                        ? registrarFederalInput.files[0] : null;
                    if (!arquivo) {
                        showToast('Selecione um arquivo PDF.', 'warning');
                        return;
                    }
                    if (!arquivo.name.toLowerCase().endsWith('.pdf')) {
                        showToast('Envie um arquivo PDF.', 'warning');
                        return;
                    }
                    const fd = new FormData();
                    fd.append('arquivo', arquivo);
                    const htmlOrig = btnRegistrarFederalEnviar.innerHTML;
                    btnRegistrarFederalEnviar.disabled = true;
                    btnRegistrarFederalEnviar.innerHTML = 'Registrando...';
                    // O wrapper global de fetch (base.html) injeta X-CSRFToken; nao setar
                    // Content-Type para o browser definir o boundary do multipart.
                    fetch(`/certidao/federal/registrar/${registrarFederalId}`, { method: 'POST', body: fd })
                        .then(function (r) { return r.json(); })
                        .then(function (data) {
                            if (data.status === 'success') {
                                showToast('Certidão Federal registrada!', 'success');
                            } else if (data.status === 'pendente') {
                                showToast(data.mensagem || data.message
                                    || 'Certidão Federal positiva: marcada como pendente.', 'warning');
                            } else {
                                showToast(appendRequestId(
                                    data.message || data.mensagem || 'Falha ao registrar a certidão.',
                                    data.request_id), 'error');
                                return;
                            }
                            if (registrarFederalModal) registrarFederalModal.hide();
                            setTimeout(function () { window.location.reload(); }, 1000);
                        })
                        .catch(function () {
                            showToast('Erro ao registrar a certidão Federal.', 'error');
                        })
                        .finally(function () {
                            btnRegistrarFederalEnviar.disabled = false;
                            btnRegistrarFederalEnviar.innerHTML = htmlOrig;
                        });
                });
            }

            if (confirmModalElement) {
                confirmModalElement.addEventListener('shown.bs.modal', function () {
                    hideLoading();
                    ensureModalBackdrop();
                });
                confirmModalElement.addEventListener('hidden.bs.modal', function () {
                    federalPendingConfirmation = false;
                });
            }

            if (pendenteModalElement) {
                pendenteModalElement.addEventListener('shown.bs.modal', function () {
                    hideLoading();
                    ensureModalBackdrop();
                });
            }

            window.addEventListener('beforeunload', function () {
                if (federalMonitorAtivo) {
                    navigator.sendBeacon('/certidao/monitorar_download_federal/stop');
                }
            });

            // BOTAO DOSSIE (download direto): o <a> baixa o PDF instantaneamente, sem evento
            // de conclusao. Damos um spinner de duracao minima perceptivel (~900ms) so como
            // reconhecimento do clique e trava anti-clique-duplo; a confirmacao real e o
            // proprio arquivo na barra de downloads (sem toast "Gerando", que ficaria mentindo).
            document.addEventListener('click', function (event) {
                const dossieBtn = event.target.closest('.btn-dossie');
                if (!dossieBtn || dossieBtn.classList.contains('is-loading')) return;

                const originalHTML = dossieBtn.innerHTML;
                dossieBtn.classList.add('is-loading', 'disabled');
                dossieBtn.setAttribute('aria-disabled', 'true');
                dossieBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Gerando…';

                setTimeout(function () {
                    dossieBtn.innerHTML = originalHTML;
                    dossieBtn.classList.remove('is-loading', 'disabled');
                    dossieBtn.removeAttribute('aria-disabled');
                }, 900);
            });

            // BOTAO BAIXAR (AUTOMAÇÃO)
            const btnsBaixar = document.querySelectorAll('.btn-baixar-certidao');
            btnsBaixar.forEach(function (btn) {
                btn.addEventListener('click', function (event) {
                    event.preventDefault();
                    linhaAtualTabela = btn.closest('tr');
                    const clickedStatusEspecial = (btn.dataset.statusEspecial || '').trim();

                    const originalHTML = btn.innerHTML;
                    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
                    btn.disabled = true;

                    const pythonUrl = btn.dataset.href;

                    if (btn.dataset.tipo === 'FGTS') {
                        fgtsBatchCertidaoId = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        fgtsBatchSingleUrl = btn.dataset.href || null;
                        const cardDaLinha = btn.closest('.company-card');
                        fgtsBatchEmpresaNome = cardDaLinha && cardDaLinha.dataset.nomeEmpresa ? cardDaLinha.dataset.nomeEmpresa : '';
                        fgtsBatchTipoCert = btn.dataset.tipo || '';
                        const certidaoId = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        if (!certidaoId) {
                            showToast('Certidão FGTS inválida.', 'error');
                            resetDownloadButton(btn, originalHTML);
                            return;
                        }

                        fgtsBatchScope = isPendenteStatus(clickedStatusEspecial) ? 'pendentes' : 'default';

                        fetch(`/fgts/lote/info/${certidaoId}?scope=${encodeURIComponent(fgtsBatchScope)}`)
                            .then(response => response.json())
                            .then(data => {
                                resetDownloadButton(btn, originalHTML);
                                if (!data) {
                                    showToast('Erro ao obter informações FGTS.', 'error');
                                    return;
                                }

                                const totalLote = Number(data.total || 0);
                                const scopeAtual = normalizeBatchScope(data.scope || fgtsBatchScope);
                                fgtsBatchScope = scopeAtual;

                                applyBatchModalData({
                                    scopeHintEl: fgtsBatchScopeHint,
                                    labelVencidasEl: fgtsBatchLabelVencidas,
                                    labelAVencerEl: fgtsBatchLabelAVencer,
                                    labelTotalEl: fgtsBatchLabelTotal,
                                    valueVencidasEl: fgtsBatchVencidas,
                                    valueAVencerEl: fgtsBatchAVencer,
                                    valueTotalEl: fgtsBatchTotal
                                }, data, scopeAtual);

                                // Certidao fora do escopo do lote (ex.: "Não definida", sem
                                // validade) nunca abre modal de lote: emite so a clicada.
                                const foraDoLote = data.start_incluida === false;
                                if (foraDoLote || (totalLote <= 1 && scopeAtual !== 'pendentes')) {
                                    if (!fgtsBatchSingleUrl) {
                                        showToast('URL de emissão indisponível.', 'error');
                                        return;
                                    }

                                    showLoading(fgtsBatchEmpresaNome || '', fgtsBatchTipoCert || '');
                                    fetch(fgtsBatchSingleUrl)
                                        .then(response => response.json())
                                        .then(dataSingle => {
                                            handleDownloadResponse(dataSingle);
                                        })
                                        .catch(() => {
                                            showToast('Erro ao emitir FGTS.', 'error');
                                        })
                                        .finally(() => {
                                            hideLoading();
                                        });
                                    return;
                                }
                                if (fgtsBatchEmpresaDisplay) fgtsBatchEmpresaDisplay.textContent = fgtsBatchEmpresaNome || 'esta empresa';
                                if (fgtsBatchModal) fgtsBatchModal.show();
                            })
                            .catch(() => {
                                resetDownloadButton(btn, originalHTML);
                                showToast('Erro ao obter informações FGTS.', 'error');
                            });

                        return;
                    }

                    if (btn.dataset.tipo === 'TRABALHISTA') {
                        trabalhistaBatchCertidaoId = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        trabalhistaBatchSingleUrl = btn.dataset.href || null;
                        const cardDaLinhaTb = btn.closest('.company-card');
                        trabalhistaBatchEmpresaNome = cardDaLinhaTb && cardDaLinhaTb.dataset.nomeEmpresa ? cardDaLinhaTb.dataset.nomeEmpresa : '';
                        trabalhistaBatchTipoCert = btn.dataset.tipo || '';
                        const certidaoIdTb = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        if (!certidaoIdTb) {
                            showToast('Certidão Trabalhista inválida.', 'error');
                            resetDownloadButton(btn, originalHTML);
                            return;
                        }

                        trabalhistaBatchScope = isPendenteStatus(clickedStatusEspecial) ? 'pendentes' : 'default';

                        fetch(`/trabalhista/lote/info/${certidaoIdTb}?scope=${encodeURIComponent(trabalhistaBatchScope)}`)
                            .then(response => response.json())
                            .then(data => {
                                resetDownloadButton(btn, originalHTML);
                                if (!data) {
                                    showToast('Erro ao obter informações Trabalhista.', 'error');
                                    return;
                                }

                                const totalLote = Number(data.total || 0);
                                const scopeAtual = normalizeBatchScope(data.scope || trabalhistaBatchScope);
                                trabalhistaBatchScope = scopeAtual;

                                applyBatchModalData({
                                    scopeHintEl: trabalhistaBatchScopeHint,
                                    labelVencidasEl: trabalhistaBatchLabelVencidas,
                                    labelAVencerEl: trabalhistaBatchLabelAVencer,
                                    labelTotalEl: trabalhistaBatchLabelTotal,
                                    valueVencidasEl: trabalhistaBatchVencidas,
                                    valueAVencerEl: trabalhistaBatchAVencer,
                                    valueTotalEl: trabalhistaBatchTotal
                                }, data, scopeAtual);

                                // Certidao fora do escopo do lote nunca abre modal: emite so a clicada.
                                const foraDoLote = data.start_incluida === false;
                                if (foraDoLote || (totalLote <= 1 && scopeAtual !== 'pendentes')) {
                                    if (!trabalhistaBatchSingleUrl) {
                                        showToast('URL de emissão indisponível.', 'error');
                                        return;
                                    }

                                    showLoading(trabalhistaBatchEmpresaNome || '', trabalhistaBatchTipoCert || '');
                                    fetch(trabalhistaBatchSingleUrl)
                                        .then(response => response.json())
                                        .then(dataSingle => {
                                            handleDownloadResponse(dataSingle);
                                        })
                                        .catch(() => {
                                            showToast('Erro ao emitir Trabalhista.', 'error');
                                        })
                                        .finally(() => {
                                            hideLoading();
                                        });
                                    return;
                                }
                                if (trabalhistaBatchEmpresaDisplay) trabalhistaBatchEmpresaDisplay.textContent = trabalhistaBatchEmpresaNome || 'esta empresa';
                                if (trabalhistaBatchModal) trabalhistaBatchModal.show();
                            })
                            .catch(() => {
                                resetDownloadButton(btn, originalHTML);
                                showToast('Erro ao obter informações Trabalhista.', 'error');
                            });

                        return;
                    }

                    const cardDaLinha = btn.closest('.company-card');
                    const estadoEmpresa = ((cardDaLinha && cardDaLinha.dataset.estadoEmpresa) ? cardDaLinha.dataset.estadoEmpresa : '').toUpperCase();
                    const isEstadualRs = btn.dataset.tipo === 'ESTADUAL' && estadoEmpresa === 'RS';

                    if (isEstadualRs) {
                        rsBatchCertidaoId = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        rsBatchSingleUrl = btn.dataset.href || null;
                        rsBatchEmpresaNome = cardDaLinha && cardDaLinha.dataset.nomeEmpresa ? cardDaLinha.dataset.nomeEmpresa : '';
                        rsBatchTipoCert = btn.dataset.tipo || '';

                        if (!rsBatchCertidaoId) {
                            showToast('Certidão Estadual RS inválida.', 'error');
                            resetDownloadButton(btn, originalHTML);
                            return;
                        }

                        rsBatchScope = isPendenteStatus(clickedStatusEspecial) ? 'pendentes' : 'default';

                        fetch(`/estadual-rs/lote/info/${rsBatchCertidaoId}?scope=${encodeURIComponent(rsBatchScope)}`)
                            .then(response => response.json())
                            .then(data => {
                                resetDownloadButton(btn, originalHTML);
                                if (!data) {
                                    showToast('Erro ao obter informações do lote Estadual RS.', 'error');
                                    return;
                                }

                                const totalLote = Number(data.total || 0);
                                const scopeAtual = normalizeBatchScope(data.scope || rsBatchScope);
                                rsBatchScope = scopeAtual;

                                applyBatchModalData({
                                    scopeHintEl: rsBatchScopeHint,
                                    labelVencidasEl: rsBatchLabelVencidas,
                                    labelAVencerEl: rsBatchLabelAVencer,
                                    labelTotalEl: rsBatchLabelTotal,
                                    valueVencidasEl: rsBatchVencidas,
                                    valueAVencerEl: rsBatchAVencer,
                                    valueTotalEl: rsBatchTotal
                                }, data, scopeAtual);

                                const foraDoLote = data.start_incluida === false;
                                if (foraDoLote || (totalLote <= 1 && scopeAtual !== 'pendentes')) {
                                    if (!rsBatchSingleUrl) {
                                        showToast('URL de emissão indisponível.', 'error');
                                        return;
                                    }

                                    showLoading(rsBatchEmpresaNome || '', rsBatchTipoCert || '');
                                    fetch(rsBatchSingleUrl)
                                        .then(response => response.json())
                                        .then(dataSingle => {
                                            handleDownloadResponse(dataSingle);
                                        })
                                        .catch(() => {
                                            showToast('Erro ao emitir Estadual RS.', 'error');
                                        })
                                        .finally(() => {
                                            hideLoading();
                                        });
                                    return;
                                }
                                if (rsBatchEmpresaDisplay) rsBatchEmpresaDisplay.textContent = rsBatchEmpresaNome || 'esta empresa';
                                if (rsBatchModal) rsBatchModal.show();
                            })
                            .catch(() => {
                                resetDownloadButton(btn, originalHTML);
                                showToast('Erro ao obter informações do lote Estadual RS.', 'error');
                            });

                        return;
                    }

                    if (btn.dataset.manualOnly === '1') {
                        showToast("Para São Paulo, use o botão 'Abrir Site'.", "primary");
                        resetDownloadButton(btn, originalHTML);
                        return;
                    }

                    const cidadeEmpresaRaw = (cardDaLinha && cardDaLinha.dataset.cidadeEmpresa) ? cardDaLinha.dataset.cidadeEmpresa : '';
                    const cidadeEmpresaNorm = cidadeEmpresaRaw
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .toUpperCase();
                    const isMunicipalImbe = btn.dataset.tipo === 'MUNICIPAL' && cidadeEmpresaNorm === 'IMBE';
                    const subtipoFixado = btn.dataset.subtipo || '';

                    if (isMunicipalImbe && !btn.dataset.imbeTipoEscolhido) {
                        btn.dataset.imbeTipoEscolhido = subtipoFixado;
                    }

                    // pega empresa e tipo para mostrar no overlay
                    let empresaNome = '';
                    const card = btn.closest('.company-card');
                    if (card && card.dataset.nomeEmpresa) {
                        empresaNome = card.dataset.nomeEmpresa;
                    }
                    const tipoCert = btn.dataset.tipo || '';

                    const imbeTipoEscolhido = btn.dataset.imbeTipoEscolhido || '';
                    let pythonUrlComParams = pythonUrl;
                    if (imbeTipoEscolhido) {
                        const sep = pythonUrlComParams.includes('?') ? '&' : '?';
                        pythonUrlComParams += `${sep}imbe_tipo=${encodeURIComponent(imbeTipoEscolhido)}`;
                    }

                    const isMunicipalBatch = btn.dataset.tipo === 'MUNICIPAL'
                        && (cidadeEmpresaNorm === 'IMBE' || cidadeEmpresaNorm === 'TRAMANDAI');

                    if (isMunicipalBatch) {
                        municipalBatchCertidaoId = btn.dataset.id || btn.dataset.certidaoId || btn.getAttribute('data-certidao-id');
                        municipalBatchSingleUrl = pythonUrlComParams || null;
                        const cardLinha = btn.closest('.company-card');
                        municipalBatchEmpresaNome = cardLinha && cardLinha.dataset.nomeEmpresa
                            ? cardLinha.dataset.nomeEmpresa
                            : '';
                        municipalBatchTipoCert = btn.dataset.tipo || '';

                        if (!municipalBatchCertidaoId) {
                            showToast('Certidão Municipal inválida.', 'error');
                            resetDownloadButton(btn, originalHTML);
                            return;
                        }

                        municipalBatchScope = isPendenteStatus(clickedStatusEspecial) ? 'pendentes' : 'default';

                        fetch(`/municipal/lote/info/${municipalBatchCertidaoId}?scope=${encodeURIComponent(municipalBatchScope)}`)
                            .then(response => response.json())
                            .then(data => {
                                resetDownloadButton(btn, originalHTML);
                                if (!data) {
                                    showToast('Erro ao obter informações Municipal.', 'error');
                                    return;
                                }

                                const totalLote = Number(data.total || 0);
                                const scopeAtual = normalizeBatchScope(data.scope || municipalBatchScope);
                                municipalBatchScope = scopeAtual;

                                applyBatchModalData({
                                    scopeHintEl: municipalBatchScopeHint,
                                    labelVencidasEl: municipalBatchLabelVencidas,
                                    labelAVencerEl: municipalBatchLabelAVencer,
                                    labelTotalEl: municipalBatchLabelTotal,
                                    valueVencidasEl: municipalBatchVencidas,
                                    valueAVencerEl: municipalBatchAVencer,
                                    valueTotalEl: municipalBatchTotal
                                }, data, scopeAtual);

                                const foraDoLote = data.start_incluida === false;
                                if (foraDoLote || (totalLote <= 1 && scopeAtual !== 'pendentes')) {
                                    if (!municipalBatchSingleUrl) {
                                        showToast('URL de emissão indisponível.', 'error');
                                        return;
                                    }

                                    showLoading(municipalBatchEmpresaNome || '', municipalBatchTipoCert || '');
                                    fetch(municipalBatchSingleUrl)
                                        .then(response => response.json())
                                        .then(dataSingle => {
                                            handleDownloadResponse(dataSingle);
                                        })
                                        .catch(() => {
                                            showToast('Erro ao emitir Municipal.', 'error');
                                        })
                                        .finally(() => {
                                            hideLoading();
                                        });
                                    return;
                                }
                                if (municipalBatchEmpresaDisplay) municipalBatchEmpresaDisplay.textContent = municipalBatchEmpresaNome || 'esta empresa';
                                if (municipalBatchModal) municipalBatchModal.show();
                            })
                            .catch(() => {
                                resetDownloadButton(btn, originalHTML);
                                showToast('Erro ao obter informações do lote Municipal.', 'error');
                            });

                        return;
                    }

                    showLoading(empresaNome, tipoCert);

                    fetch(pythonUrlComParams)
                        .then(response => response.json())
                        .then(data => {
                            resetDownloadButton(btn, originalHTML);

                            handleDownloadResponse(data);
                        })
                        .catch(error => {
                            console.error('Erro:', error);
                            resetDownloadButton(btn, originalHTML);
                            showToast("Erro de comunicação.", "error");
                        })
                        .finally(() => {
                            hideLoading();
                        });
                });
            });

            // Componente A (composição) do desfecho de um lote. Os ids saem do
            // macro zl_comp_lote(prefixo), e por isso o painel de andamento e o
            // resumo do lote são lidos e escritos pelo MESMO par de funções —
            // duas cópias divergiriam justamente onde o operador compara.
            function elementosComp(prefixo) {
                const el = (sufixo) => document.getElementById(`${prefixo}${sufixo}`);
                return {
                    segOk: el('SegOk'),
                    segPend: el('SegPend'),
                    segFalhas: el('SegFalhas'),
                    emitidas: el('Emitidas'),
                    pendentes: el('Pendentes'),
                    falhas: el('Falhas'),
                };
            }

            function atualizarComp(comp, total, emitidas, pendentes, falhas) {
                if (!comp) return;

                [[comp.emitidas, emitidas], [comp.pendentes, pendentes], [comp.falhas, falhas]]
                    .forEach(([el, valor]) => {
                        if (!el) return;
                        el.textContent = valor;
                        const stat = el.closest('.zl-comp-stat');
                        if (stat) stat.dataset.zero = valor > 0 ? '0' : '1';
                    });

                // A barra é a composição do desfecho sobre o total: o trilho que
                // sobra é o que falta. is-vazio impede o fio divisório de 1px de
                // aparecer solto onde não há segmento.
                [[comp.segOk, emitidas], [comp.segPend, pendentes], [comp.segFalhas, falhas]]
                    .forEach(([el, valor]) => {
                        if (!el) return;
                        el.style.width = total > 0 ? `${(valor / total) * 100}%` : '0%';
                        el.classList.toggle('is-vazio', valor <= 0);
                    });
            }

            // === dock do lote minimizado =============================
            // Um só para os 4 lotes: o guarda global do servidor permite uma
            // automação por vez, então nunca há dois para mostrar.
            const dockEl = document.getElementById('lote-dock');
            const dock = dockEl ? {
                tipo: document.getElementById('dockTipo'),
                fracao: document.getElementById('dockFracao'),
                relogio: document.getElementById('dockRelogio'),
                comp: elementosComp('dock'),
            } : null;
            // qual lote está minimizado agora (null = nenhum). Guarda o config
            // para o botão restaurar saber qual overlay reabrir.
            let loteMinimizado = null;
            // slug -> config do lote, preenchido por startBatchPolling/registro.
            // O botao Minimizar vive dentro do macro do painel e so conhece o
            // slug; e aqui que ele vira o config correspondente.
            const configPorSlug = {};

            function minimizarLote(config, titulo) {
                if (!dockEl || !config) return;
                loteMinimizado = config;
                if (config.overlayEl) config.overlayEl.classList.add('d-none');
                if (dock.tipo) dock.tipo.textContent = titulo || 'Lote';
                dockEl.classList.remove('d-none');
                // o polling NÃO é interrompido: ele nunca dependeu do overlay
                // estar visível, só escrevia nele.
            }

            function restaurarLote() {
                if (!dockEl) return;
                const config = loteMinimizado;
                loteMinimizado = null;
                dockEl.classList.add('d-none');
                dockEl.classList.remove('is-paused');
                if (config && config.overlayEl) config.overlayEl.classList.remove('d-none');
            }

            function esconderDock() {
                if (!dockEl) return;
                loteMinimizado = null;
                dockEl.classList.add('d-none');
                dockEl.classList.remove('is-paused', 'is-recusa');
            }

            // Pisca o dock quando o servidor recusa uma ação por automação em
            // curso (409). Sem isto o operador vê só um toast e não liga a
            // negativa ao lote que está rodando fora de vista.
            function sinalizarRecusaNoDock() {
                if (!dockEl || dockEl.classList.contains('d-none')) return;
                dockEl.classList.remove('is-recusa');
                void dockEl.offsetWidth;   // reinicia a animação
                dockEl.classList.add('is-recusa');
            }

            function atualizarDock(data, total, concluidas, emitidas, pendentes, falhas) {
                if (!dock || !dockEl || dockEl.classList.contains('d-none')) return;
                if (dock.fracao) dock.fracao.textContent = `${concluidas}/${total}`;
                if (dock.relogio) {
                    dock.relogio.textContent = data.status === 'paused'
                        ? 'pausado'
                        : formatarRelogio(segundosDesde(data.started_at));
                }
                dockEl.classList.toggle('is-paused', data.status === 'paused');
                atualizarComp(dock.comp, total, emitidas, pendentes, falhas);
            }

            if (dockEl) {
                const btnRestaurar = document.getElementById('btnDockRestaurar');
                if (btnRestaurar) btnRestaurar.addEventListener('click', restaurarLote);

                document.querySelectorAll('.btn-minimizar-lote').forEach((btn) => {
                    btn.addEventListener('click', () => {
                        minimizarLote(configPorSlug[btn.dataset.slug], btn.dataset.titulo);
                    });
                });
            }

            // Painel de andamento: o componente A mais o que só ele tem
            // (relógio, linha viva, fração e o anúncio para leitor de tela).
            function elementosPainelLote(slug) {
                const el = (sufixo) => document.getElementById(`${slug}Batch${sufixo}`);
                return {
                    comp: elementosComp(`${slug}Batch`),
                    concluidas: el('Progress'),
                    total: el('TotalItens'),
                    relogio: el('Elapsed'),
                    live: el('Live'),
                    liveTexto: el('LastMessage'),
                    anuncio: el('Anuncio'),
                };
            }

            // O backend grava started_at em UTC sem tzinfo (utils.utcnow_naive),
            // e sem o "Z" o navegador leria como hora local — o relógio nasceria
            // com horas de atraso. calcularTempoLote não sofre disso porque
            // subtrai duas datas da mesma origem.
            function segundosDesde(isoUtcNaive) {
                if (!isoUtcNaive) return 0;
                const temFuso = /(Z|[+-]\d{2}:?\d{2})$/.test(isoUtcNaive);
                const inicio = new Date(temFuso ? isoUtcNaive : `${isoUtcNaive}Z`);
                if (Number.isNaN(inicio.getTime())) return 0;
                return Math.max(0, Math.floor((Date.now() - inicio.getTime()) / 1000));
            }

            function formatarRelogio(segundos) {
                const min = Math.floor(segundos / 60);
                const sec = segundos % 60;
                return `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
            }

            // O que o operador reconhece é o nome do cliente, não o id da
            // certidão. A linha da certidão vive dentro do card da empresa,
            // que carrega o nome em data-nome-empresa.
            function nomeEmpresaDaCertidao(certidaoId) {
                if (!certidaoId) return '';
                const alvo = document.querySelector(
                    `.btn-baixar-certidao[data-id="${certidaoId}"], .btn-abrir-site[data-id="${certidaoId}"]`
                );
                const card = alvo ? alvo.closest('.company-card') : null;
                return card ? (card.dataset.nomeEmpresa || '') : '';
            }

            function calcularTempoLote(data) {
                if (!data || !data.started_at || !data.finished_at) return '0s';
                const ini = new Date(data.started_at);
                const fim = new Date(data.finished_at);
                const diff = Math.max(0, Math.floor((fim - ini) / 1000));
                const min = Math.floor(diff / 60);
                const sec = diff % 60;
                return min > 0 ? `${min}m ${sec}s` : `${sec}s`;
            }

            function calcularTaxaSucesso(success, total) {
                const sucessoNum = Number(success || 0);
                const totalNum = Number(total || 0);
                if (totalNum <= 0) return '0%';
                return `${Math.round((sucessoNum / totalNum) * 100)}%`;
            }

            function resolveLastMessage(data) {
                if (!data || !Array.isArray(data.last_messages)) return null;
                if (!data.last_messages.length) return null;
                return data.last_messages[data.last_messages.length - 1];
            }

            // Linha viva do painel. Em operação normal ela diz quem está sendo
            // emitida agora; quando a última mensagem do lote é warning/error,
            // o aviso toma a linha — é o que o operador precisa ler.
            function aplicarLinhaViva(painel, data) {
                if (!painel || !painel.live || !painel.liveTexto) return;

                const ultima = resolveLastMessage(data);
                const nivel = (ultima && ultima.level || '').toString().toLowerCase();
                const pausado = data.status === 'paused';

                painel.live.classList.remove('is-warning', 'is-error', 'is-paused');

                if (nivel === 'warning' || nivel === 'error') {
                    painel.live.classList.add(nivel === 'error' ? 'is-error' : 'is-warning');
                    painel.liveTexto.textContent = ultima.message;
                    painel.liveTexto.title = ultima.message;
                    return;
                }

                if (pausado) {
                    painel.live.classList.add('is-paused');
                    painel.liveTexto.textContent = 'Lote pausado';
                    painel.liveTexto.title = '';
                    return;
                }

                const empresa = nomeEmpresaDaCertidao(data.current_id);
                if (empresa) {
                    painel.liveTexto.innerHTML = '';
                    painel.liveTexto.append('Emitindo ');
                    const forte = document.createElement('strong');
                    forte.textContent = empresa;
                    painel.liveTexto.append(forte);
                    painel.liveTexto.title = empresa;
                    return;
                }

                painel.liveTexto.textContent = (ultima && ultima.message) || 'Emitindo…';
                painel.liveTexto.title = '';
            }

            function limparPainelLote(painel, total) {
                if (!painel) return;
                atualizarPainelLote(painel, {
                    status: 'running',
                    total: total || 0,
                    index: 0,
                    success: 0,
                    pendentes_resultado: 0,
                    falhas: 0,
                    last_messages: [],
                });
                if (painel.relogio) painel.relogio.textContent = '00:00';
                if (painel.liveTexto) {
                    painel.liveTexto.textContent = 'Preparando o lote…';
                    painel.liveTexto.title = '';
                }
            }

            function atualizarPainelLote(painel, data) {
                if (!painel) return;

                const total = Number(data.total || 0);
                const concluidas = Number(data.index || 0);
                const sucessos = Number(data.success || 0);
                const pendentes = Number(data.pendentes_resultado || 0);
                const falhas = Number(data.falhas || 0);

                if (painel.concluidas) painel.concluidas.textContent = concluidas;
                if (painel.total) painel.total.textContent = total;

                atualizarComp(painel.comp, total, sucessos, pendentes, falhas);
                atualizarDock(data, total, concluidas, sucessos, pendentes, falhas);

                if (painel.relogio && data.started_at) {
                    painel.relogio.textContent = formatarRelogio(segundosDesde(data.started_at));
                }

                aplicarLinhaViva(painel, data);

                if (painel.anuncio) {
                    painel.anuncio.textContent =
                        `${concluidas} de ${total} concluídas. `
                        + `${sucessos} emitidas, ${pendentes} pendentes, ${falhas} falhas.`;
                }
            }


            function atualizarUltimaLinhaConcluida(data, getLastId, setLastId) {
                if (!(data && data.last_completed && data.last_completed.certidao_id)) return;

                const lastId = data.last_completed.certidao_id;
                if (lastId === getLastId()) return;

                setLastId(lastId);
                const btnLinha = document.querySelector(`.btn-baixar-certidao[data-id="${lastId}"]`);
                if (!btnLinha) return;

                const linha = btnLinha.closest('tr');
                const novaDataTexto = data.last_completed.data_formatada || 'Não definida';
                const novaClasse = data.last_completed.nova_classe || 'status-cinza';
                atualizarLinhaUI(linha, novaClasse, novaDataTexto);
            }

            function startBatchPolling(config) {
                const pollerAtual = config.getPoller();
                if (pollerAtual) clearInterval(pollerAtual);

                const poller = setInterval(() => {
                    fetch(config.endpoints.status)
                        .then(r => r.json())
                        .then(data => {
                            if (!data) return;

                            atualizarPainelLote(config.painel, data);

                            atualizarUltimaLinhaConcluida(data, config.getLastCompletedId, config.setLastCompletedId);

                            if (config.resumeBtn) {
                                if (data.status === 'paused') config.resumeBtn.classList.remove('d-none');
                                else config.resumeBtn.classList.add('d-none');
                            }

                            if (data.status === 'completed') {
                                clearInterval(config.getPoller());
                                config.setPoller(null);
                                if (config.overlayEl) config.overlayEl.classList.add('d-none');
                                esconderDock();
                                showToast(config.messages.completed, 'success');

                                const success = Number(data.success || 0);
                                const falhas = Number(data.falhas || 0);
                                const pendentes = Number(data.pendentes_resultado || 0);
                                const total = Number(data.total || 0);
                                const scopeAtual = normalizeBatchScope(
                                    data.scope || (config.getBatchScope ? config.getBatchScope() : 'default')
                                );

                                // Mesmo componente A do painel de andamento, com
                                // os mesmos números — o operador acabou de olhar
                                // para ele e não pode ver outro desenho aqui.
                                atualizarComp(config.summaryComp, total, success, pendentes, falhas);

                                if (config.summaryTotalEl) config.summaryTotalEl.textContent = total;
                                if (config.summaryTempoEl) config.summaryTempoEl.textContent = calcularTempoLote(data);
                                // Taxa de sucesso = itens concluídos sem erro técnico
                                // (emitidas + pendentes) / total. Uma certidão pendente
                                // é um desfecho legítimo da automação, não uma falha.
                                if (config.summaryTaxaEl) config.summaryTaxaEl.textContent = calcularTaxaSucesso(success + pendentes, total);

                                if (config.summaryNoticeEl) {
                                    const qtdPendentes = Number(data.fgts_marcadas_pendente || 0);
                                    if (qtdPendentes > 0 && scopeAtual === 'default') {
                                        config.summaryNoticeEl.textContent = `${qtdPendentes} certidão(ões) não puderam ser emitidas automaticamente no FGTS e foram marcadas como pendente.`;
                                        config.summaryNoticeEl.classList.remove('d-none');
                                    } else {
                                        config.summaryNoticeEl.textContent = '';
                                        config.summaryNoticeEl.classList.add('d-none');
                                    }
                                }

                                if (config.onCompleted) config.onCompleted(data);
                                if (config.summaryModal) config.summaryModal.show();
                                return;
                            }

                            if (data.status === 'error') {
                                clearInterval(config.getPoller());
                                config.setPoller(null);
                                if (config.overlayEl) config.overlayEl.classList.add('d-none');
                                esconderDock();
                                showToast(buildErrorMessage(data, config.messages.error), 'error');
                                return;
                            }

                            if (data.status === 'stopped') {
                                clearInterval(config.getPoller());
                                config.setPoller(null);
                                if (config.overlayEl) config.overlayEl.classList.add('d-none');
                                esconderDock();
                                showToast(config.messages.stopped, 'primary');
                            }
                        });
                }, 1500);

                config.setPoller(poller);
            }

            function bindBatchControls(config) {
                if (config.slug) configPorSlug[config.slug] = config;
                if (config.startBtn) {
                    let iniciandoLote = false;
                    config.startBtn.addEventListener('click', function () {
                        if (!config.getCertidaoId()) {
                            showToast(config.messages.invalidCertidao, 'error');
                            return;
                        }

                        // Bloqueia duplo-clique: o modal fica aberto ate a resposta
                        // chegar, e um 2o POST /iniciar veria o lote ja 'running' e
                        // retornaria "lote em andamento" sobrescrevendo a UI de sucesso.
                        if (iniciandoLote) return;
                        iniciandoLote = true;
                        config.startBtn.disabled = true;

                        fetch(config.endpoints.start, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                certidao_id: config.getCertidaoId(),
                                scope: config.getBatchScope ? config.getBatchScope() : 'default'
                            })
                        })
                            .then(r => r.json())
                            .then(data => {
                                if (data.status !== 'ok') {
                                    if (data.motivo === 'automacao_em_curso') sinalizarRecusaNoDock();
                                    showToast(buildErrorMessage(data, config.messages.startError), 'error');
                                    return;
                                }
                                if (config.batchModal) config.batchModal.hide();
                                // zera o painel antes de abrir: o primeiro poll
                                // só chega em ~1,5s e os números do lote
                                // anterior piscariam nesse intervalo.
                                limparPainelLote(config.painel, Number(data.total || 0));
                                if (config.overlayEl) config.overlayEl.classList.remove('d-none');
                                startBatchPolling(config);
                            })
                            .catch(() => showToast(config.messages.startCatchError, 'error'))
                            .finally(() => {
                                iniciandoLote = false;
                                config.startBtn.disabled = false;
                            });
                    });
                }

                if (config.singleBtn) {
                    config.singleBtn.addEventListener('click', function () {
                        const singleUrl = config.getSingleUrl();
                        if (!singleUrl) {
                            showToast('URL de emissão indisponível.', 'error');
                            return;
                        }

                        if (config.batchModal) config.batchModal.hide();
                        showLoading(config.getEmpresaNome() || '', config.getTipoCert() || '');

                        fetch(singleUrl)
                            .then(response => response.json())
                            .then(data => {
                                handleDownloadResponse(data);
                            })
                            .catch(() => {
                                showToast(config.messages.singleError, 'error');
                            })
                            .finally(() => {
                                hideLoading();
                            });
                    });
                }

                if (config.pauseBtn) {
                    config.pauseBtn.addEventListener('click', function () {
                        fetch(config.endpoints.pause, { method: 'POST' })
                            .then(r => r.json())
                            .then(data => {
                                showToast(data.message || config.messages.paused, 'primary');
                                if (config.resumeBtn) config.resumeBtn.classList.remove('d-none');
                            });
                    });
                }

                if (config.resumeBtn) {
                    config.resumeBtn.addEventListener('click', function () {
                        fetch(config.endpoints.resume, { method: 'POST' })
                            .then(r => r.json())
                            .then(data => {
                                if (data.status !== 'ok') {
                                    showToast(buildErrorMessage(data, config.messages.resumeError), 'error');
                                    return;
                                }
                                showToast(config.messages.resumed, 'success');
                            });
                    });
                }

                if (config.stopBtn) {
                    config.stopBtn.addEventListener('click', function () {
                        fetch(config.endpoints.stop, { method: 'POST' })
                            .then(r => r.json())
                            .then(data => {
                                showToast(data.message || config.messages.stopped, 'primary');
                                if (config.overlayEl) config.overlayEl.classList.add('d-none');
                                esconderDock();
                            });
                    });
                }
            }

            bindBatchControls({
                endpoints: {
                    status: '/fgts/lote/status',
                    start: '/fgts/lote/iniciar',
                    pause: '/fgts/lote/pausar',
                    resume: '/fgts/lote/retomar',
                    stop: '/fgts/lote/parar'
                },
                messages: {
                    invalidCertidao: 'Certidão FGTS inválida.',
                    startError: 'Erro ao iniciar lote.',
                    startCatchError: 'Erro ao iniciar lote FGTS.',
                    singleError: 'Erro ao emitir FGTS.',
                    paused: 'Lote pausado.',
                    resumeError: 'Erro ao retomar lote.',
                    resumed: 'Lote FGTS retomado.',
                    stopped: 'Lote FGTS interrompido.',
                    completed: 'Lote FGTS concluído.',
                    error: 'Erro grave no lote FGTS.'
                },
                getPoller: () => fgtsBatchPoller,
                setPoller: (value) => { fgtsBatchPoller = value; },
                getLastCompletedId: () => fgtsBatchLastCompletedId,
                setLastCompletedId: (value) => { fgtsBatchLastCompletedId = value; },
                getCertidaoId: () => fgtsBatchCertidaoId,
                getBatchScope: () => fgtsBatchScope,
                getSingleUrl: () => fgtsBatchSingleUrl,
                getEmpresaNome: () => fgtsBatchEmpresaNome,
                getTipoCert: () => fgtsBatchTipoCert,
                painel: fgtsPainel,
                slug: 'fgts',
                overlayEl: fgtsBatchOverlay,
                resumeBtn: btnFgtsBatchResume,
                pauseBtn: btnFgtsBatchPause,
                stopBtn: btnFgtsBatchStop,
                startBtn: btnFgtsBatchStart,
                singleBtn: btnFgtsSingleEmit,
                batchModal: fgtsBatchModal,
                summaryModal: fgtsBatchSummaryModal,
                summaryComp: elementosComp('fgtsBatchSummary'),
                summaryTotalEl: fgtsBatchSummaryTotal,
                summaryTempoEl: fgtsBatchSummaryTempo,
                summaryTaxaEl: fgtsBatchSummaryTaxa,
                summaryNoticeEl: fgtsBatchSummaryNotice
            });

            bindBatchControls({
                endpoints: {
                    status: '/trabalhista/lote/status',
                    start: '/trabalhista/lote/iniciar',
                    pause: '/trabalhista/lote/pausar',
                    resume: '/trabalhista/lote/retomar',
                    stop: '/trabalhista/lote/parar'
                },
                messages: {
                    invalidCertidao: 'Certidão Trabalhista inválida.',
                    startError: 'Erro ao iniciar lote Trabalhista.',
                    startCatchError: 'Erro ao iniciar lote Trabalhista.',
                    singleError: 'Erro ao emitir Trabalhista.',
                    paused: 'Lote Trabalhista pausado.',
                    resumeError: 'Erro ao retomar lote Trabalhista.',
                    resumed: 'Lote Trabalhista retomado.',
                    stopped: 'Lote Trabalhista interrompido.',
                    completed: 'Lote Trabalhista concluído.',
                    error: 'Erro grave no lote Trabalhista.'
                },
                getPoller: () => trabalhistaBatchPoller,
                setPoller: (value) => { trabalhistaBatchPoller = value; },
                getLastCompletedId: () => trabalhistaBatchLastCompletedId,
                setLastCompletedId: (value) => { trabalhistaBatchLastCompletedId = value; },
                getCertidaoId: () => trabalhistaBatchCertidaoId,
                getBatchScope: () => trabalhistaBatchScope,
                getSingleUrl: () => trabalhistaBatchSingleUrl,
                getEmpresaNome: () => trabalhistaBatchEmpresaNome,
                getTipoCert: () => trabalhistaBatchTipoCert,
                painel: trabalhistaPainel,
                slug: 'trabalhista',
                overlayEl: trabalhistaBatchOverlay,
                resumeBtn: btnTrabalhistaBatchResume,
                pauseBtn: btnTrabalhistaBatchPause,
                stopBtn: btnTrabalhistaBatchStop,
                startBtn: btnTrabalhistaBatchStart,
                singleBtn: btnTrabalhistaSingleEmit,
                batchModal: trabalhistaBatchModal,
                summaryModal: trabalhistaBatchSummaryModal,
                summaryComp: elementosComp('trabalhistaBatchSummary'),
                summaryTotalEl: trabalhistaBatchSummaryTotal,
                summaryTempoEl: trabalhistaBatchSummaryTempo,
                summaryTaxaEl: trabalhistaBatchSummaryTaxa,
                summaryNoticeEl: trabalhistaBatchSummaryNotice
            });

            bindBatchControls({
                endpoints: {
                    status: '/estadual-rs/lote/status',
                    start: '/estadual-rs/lote/iniciar',
                    pause: '/estadual-rs/lote/pausar',
                    resume: '/estadual-rs/lote/retomar',
                    stop: '/estadual-rs/lote/parar'
                },
                messages: {
                    invalidCertidao: 'Certidão Estadual RS inválida.',
                    startError: 'Erro ao iniciar lote Estadual RS.',
                    startCatchError: 'Erro ao iniciar lote Estadual RS.',
                    singleError: 'Erro ao emitir Estadual RS.',
                    paused: 'Lote Estadual RS pausado.',
                    resumeError: 'Erro ao retomar lote Estadual RS.',
                    resumed: 'Lote Estadual RS retomado.',
                    stopped: 'Lote Estadual RS interrompido.',
                    completed: 'Lote Estadual RS concluído.',
                    error: 'Erro grave no lote Estadual RS.'
                },
                getPoller: () => rsBatchPoller,
                setPoller: (value) => { rsBatchPoller = value; },
                getLastCompletedId: () => rsBatchLastCompletedId,
                setLastCompletedId: (value) => { rsBatchLastCompletedId = value; },
                getCertidaoId: () => rsBatchCertidaoId,
                getBatchScope: () => rsBatchScope,
                getSingleUrl: () => rsBatchSingleUrl,
                getEmpresaNome: () => rsBatchEmpresaNome,
                getTipoCert: () => rsBatchTipoCert,
                painel: rsPainel,
                slug: 'rs',
                overlayEl: rsBatchOverlay,
                resumeBtn: btnRsBatchResume,
                pauseBtn: btnRsBatchPause,
                stopBtn: btnRsBatchStop,
                startBtn: btnRsBatchStart,
                singleBtn: btnRsSingleEmit,
                batchModal: rsBatchModal,
                summaryModal: rsBatchSummaryModal,
                summaryComp: elementosComp('rsBatchSummary'),
                summaryTotalEl: rsBatchSummaryTotal,
                summaryTempoEl: rsBatchSummaryTempo,
                summaryTaxaEl: rsBatchSummaryTaxa,
                onCompleted: (data) => {
                    if (rsBatchSummaryPositivas) rsBatchSummaryPositivas.textContent = data.positivas || 0;
                    if (rsBatchSummaryNegativas) rsBatchSummaryNegativas.textContent = data.negativas || 0;
                    if (rsBatchSummaryEfeitoNegativas) rsBatchSummaryEfeitoNegativas.textContent = data.efeito_negativas || 0;
                }
            });

            bindBatchControls({
                endpoints: {
                    status: '/municipal/lote/status',
                    start: '/municipal/lote/iniciar',
                    pause: '/municipal/lote/pausar',
                    resume: '/municipal/lote/retomar',
                    stop: '/municipal/lote/parar'
                },
                messages: {
                    invalidCertidao: 'Certidão Municipal inválida.',
                    startError: 'Erro ao iniciar lote Municipal.',
                    startCatchError: 'Erro ao iniciar lote Municipal.',
                    singleError: 'Erro ao emitir Municipal.',
                    paused: 'Lote Municipal pausado.',
                    resumeError: 'Erro ao retomar lote Municipal.',
                    resumed: 'Lote Municipal retomado.',
                    stopped: 'Lote Municipal interrompido.',
                    completed: 'Lote Municipal concluído.',
                    error: 'Erro grave no lote Municipal.'
                },
                getPoller: () => municipalBatchPoller,
                setPoller: (value) => { municipalBatchPoller = value; },
                getLastCompletedId: () => municipalBatchLastCompletedId,
                setLastCompletedId: (value) => { municipalBatchLastCompletedId = value; },
                getCertidaoId: () => municipalBatchCertidaoId,
                getBatchScope: () => municipalBatchScope,
                getSingleUrl: () => municipalBatchSingleUrl,
                getEmpresaNome: () => municipalBatchEmpresaNome,
                getTipoCert: () => municipalBatchTipoCert,
                painel: municipalPainel,
                slug: 'municipal',
                overlayEl: municipalBatchOverlay,
                resumeBtn: btnMunicipalBatchResume,
                pauseBtn: btnMunicipalBatchPause,
                stopBtn: btnMunicipalBatchStop,
                startBtn: btnMunicipalBatchStart,
                singleBtn: btnMunicipalSingleEmit,
                batchModal: municipalBatchModal,
                summaryModal: municipalBatchSummaryModal,
                summaryComp: elementosComp('municipalBatchSummary'),
                summaryTotalEl: municipalBatchSummaryTotal,
                summaryTempoEl: municipalBatchSummaryTempo,
                summaryTaxaEl: municipalBatchSummaryTaxa,
                summaryNoticeEl: municipalBatchSummaryNotice
            });

            // ações de salvar e pendente modais

            // Salvar Data (Modal Verde)
            if (btnSalvar) {
                btnSalvar.addEventListener('click', function () {
                    if (dadosParaSalvar.certidao_id) {
                        fetch('/certidao/salvar_data_confirmada', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(dadosParaSalvar)
                        })
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    if (confirmModal) confirmModal.hide();

                                    if (linhaAtualTabela) {
                                        atualizarLinhaUI(linhaAtualTabela, data.nova_classe, data.nova_data_formatada);
                                    }
                                    showToast(data.message, "success");
                                } else {
                                    showToast(
                                        appendRequestId(
                                            'Erro ao salvar: ' + (data.message || 'Erro desconhecido'),
                                            data.request_id
                                        ),
                                        "error"
                                    );
                                }
                            });
                    }
                });
            }

            // Marcar Pendente (Modal Vermelho)
            if (btnConfirmarPendente) {
                btnConfirmarPendente.addEventListener('click', function () {
                    if (certidaoIdPendente) {
                        fetch(`/certidao/marcar_pendente_json/${certidaoIdPendente}`, { method: 'POST' })
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    if (pendenteModal) pendenteModal.hide();

                                    if (linhaAtualTabela) {
                                        atualizarLinhaUI(linhaAtualTabela, 'status-vermelho', 'PENDENTE');
                                    }
                                    showToast("Certidão marcada como Pendente.", "success");
                                } else {
                                    showToast(
                                        appendRequestId(
                                            'Erro: ' + (data.message || 'Erro desconhecido'),
                                            data.request_id
                                        ),
                                        "error"
                                    );
                                }
                            });
                    }
                });
            }

            // Botao editar status certidao (amarelo)

            btnsEditar.forEach(function (btn) {
                btn.addEventListener('click', function () {
                    linhaAtualTabela = btn.closest('tr');
                    certidaoIdManual = btn.dataset.certidaoId;
                    if (editFormInput) editFormInput.value = "";
                });
            });

            // Salvar Edição Manual
            if (btnSalvarManual) {
                btnSalvarManual.addEventListener('click', function () {
                    const novaData = editFormInput.value;
                    if (!novaData) {
                        showToast("Por favor, selecione uma data.", "error");
                        return;
                    }
                    if (certidaoIdManual) {
                        fetch(`/certidao/atualizar_json/${certidaoIdManual}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ nova_validade: novaData })
                        })
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    if (editModal) editModal.hide();

                                    if (linhaAtualTabela) {
                                        atualizarLinhaUI(linhaAtualTabela, data.nova_classe, data.nova_data_formatada);
                                    }
                                    showToast(data.message, "success");
                                } else {
                                    showToast(
                                        appendRequestId(
                                            'Erro: ' + (data.message || 'Erro desconhecido'),
                                            data.request_id
                                        ),
                                        "error"
                                    );
                                }
                            })
                            .catch(error => { console.error(error); showToast('Erro de comunicação.', "error"); });
                    }
                });
            }

            // marcar pendente manual
            if (btnPendenteManual) {
                btnPendenteManual.addEventListener('click', function () {
                    if (certidaoIdManual) {
                        fetch(`/certidao/marcar_pendente_json/${certidaoIdManual}`, { method: 'POST' })
                            .then(response => response.json())
                            .then(data => {
                                if (data.status === 'success') {
                                    if (editModal) editModal.hide();

                                    if (linhaAtualTabela) {
                                        atualizarLinhaUI(linhaAtualTabela, 'status-vermelho', 'PENDENTE');
                                    }
                                    showToast("Certidão marcada como Pendente.", "success");
                                } else {
                                    showToast(
                                        appendRequestId(
                                            'Erro: ' + (data.message || 'Erro desconhecido'),
                                            data.request_id
                                        ),
                                        "error"
                                    );
                                }
                            })
                            .catch(error => { console.error(error); showToast('Erro de comunicação.', "error"); });
                    }
                });
            }

            function prepararVisualizarClicks() {
                const btnsVisualizar = document.querySelectorAll('.btn-visualizar-certidao');
                btnsVisualizar.forEach(function (btn) {
                    btn.addEventListener('click', function (event) {
                        event.preventDefault();
                        // Botões da tabela usam data-certidao-id (token gerado sob demanda)
                        const certidaoId = btn.dataset.certidaoId;
                        if (certidaoId) {
                            fetch(`/certidao/${certidaoId}/token-visualizar`)
                                .then(resp => {
                                    if (!resp.ok) {
                                        showToast('Arquivo não encontrado para esta certidão.', 'error');
                                        return null;
                                    }
                                    return resp.json();
                                })
                                .then(data => {
                                    if (!data) return;
                                    const novaAba = window.open(data.url, '_blank', 'noopener');
                                    if (novaAba) novaAba.opener = null;
                                })
                                .catch(() => showToast('Erro ao localizar o arquivo.', 'error'));
                            return;
                        }
                        // Fallback: botão do modal usa data-url definido por atualizarBotaoVisualizar
                        const url = btn.dataset.url;
                        if (!url) {
                            showToast('Arquivo não encontrado para esta certidão.', 'error');
                            return;
                        }
                        fetch(url, { method: 'HEAD' })
                            .then(resp => {
                                if (resp.ok) {
                                    const novaAba = window.open(url, '_blank', 'noopener');
                                    if (novaAba) novaAba.opener = null;
                                } else {
                                    showToast('Arquivo não encontrado para esta certidão.', 'error');
                                }
                            })
                            .catch(() => showToast('Erro ao localizar o arquivo.', 'error'));
                    });
                });
            }

            prepararVisualizarClicks();

            // filtro de pesquisa nome
            const filtroInput = document.getElementById('filtroEmpresa');
            if (filtroInput) {
                filtroInput.addEventListener('input', () => {
                    const termoBusca = filtroInput.value.toLowerCase().trim();
                    const cardsEmpresa = document.querySelectorAll('.company-card');

                    cardsEmpresa.forEach(card => {
                        if (card.dataset.nomeEmpresa) {
                            const nomeEmpresa = card.dataset.nomeEmpresa.toLowerCase();

                            if (nomeEmpresa.includes(termoBusca)) {
                                card.classList.remove('search-hidden');
                                card.dataset.searchHidden = '0';
                            } else {
                                card.classList.add('search-hidden');
                                card.dataset.searchHidden = '1';
                            }
                        }
                    });

                    atualizarContagensChips();
                });
            }

            const formFiltro = document.getElementById('filtro-form');
            const ordemRadios = Array.from(document.querySelectorAll('.btn-check[name="ordem-opt"]'));
            const ordemBadge = document.getElementById('ordem-dd-badge');
            const ordemHidden = document.getElementById('ordem-hidden');
            const ordemStorageKey = 'dashboardOrdem';

            // Direcao do modo "Atividade": 'paradas' (padrao, ISO asc — parada ha mais
            // tempo no topo) ou 'recentes' (ISO desc — atualizada agora no topo).
            const dirToggle = document.getElementById('ordem-atividade-dir');
            const dirToggleLabel = document.getElementById('ordem-atividade-dir-label');
            const ordemDirStorageKey = 'dashboardOrdemAtividadeDir';
            let dirAtividade = 'paradas';

            function getOrdemAtual() {
                const sel = ordemRadios.find(r => r.checked);
                return sel ? sel.value : 'urgencia';
            }

            // Mostra o toggle so no modo Atividade e reflete a direcao atual (label +
            // aria-pressed). A direcao e puramente client-side (o server ordena por id).
            function atualizarDirToggleUI() {
                if (!dirToggle) return;
                dirToggle.hidden = getOrdemAtual() !== 'atualizacao';
                const recentes = dirAtividade === 'recentes';
                dirToggle.setAttribute('aria-pressed', recentes ? 'true' : 'false');
                if (dirToggleLabel) {
                    dirToggleLabel.textContent = recentes ? 'Recentes primeiro' : 'Paradas primeiro';
                }
            }

            function atualizarBadgeOrdem() {
                if (!ordemBadge) return;
                const sel = ordemRadios.find(r => r.checked);
                const lbl = sel ? document.querySelector('label[for="' + sel.id + '"]') : null;
                let texto = lbl ? (lbl.dataset.ordemLabel || lbl.textContent.trim()) : 'Urgência';
                if (sel && sel.value === 'atualizacao') {
                    texto += dirAtividade === 'recentes' ? ' · recentes 1º' : ' · paradas 1º';
                }
                ordemBadge.textContent = texto;
            }

            const todasCheckbox = document.getElementById('status-todas');
            const statusCheckboxes = [
                document.getElementById('status-validas'),
                document.getElementById('status-a-vencer'),
                document.getElementById('status-vencidas'),
                document.getElementById('status-pendentes'),
                document.getElementById('status-nao-definida')
            ];

            const tipoTodasCheckbox = document.getElementById('tipo-todas');
            const tipoCheckboxes = [
                document.getElementById('tipo-federal'),
                document.getElementById('tipo-fgts'),
                document.getElementById('tipo-estadual'),
                document.getElementById('tipo-municipal'),
                document.getElementById('tipo-trabalhista')
            ];

            const estadoTodasCheckbox = document.getElementById('estado-todas');
            const estadoCheckboxes = Array.from(
                document.querySelectorAll('.btn-check[id^="estado-"]:not(#estado-todas)')
            );
            const cidadeTodasCheckbox = document.getElementById('cidade-todas');
            const cidadeCheckboxes = Array.from(
                document.querySelectorAll('.btn-check[id^="cidade-"]:not(#cidade-todas)')
            );

            function aplicarOrdenacaoSalva() {
                if (!ordemRadios.length) return;
                const ordemSalva = localStorage.getItem(ordemStorageKey);
                if (ordemSalva) {
                    const alvo = ordemRadios.find(r => r.value === ordemSalva);
                    if (alvo) alvo.checked = true;
                } else {
                    localStorage.setItem(ordemStorageKey, getOrdemAtual());
                }
                if (ordemHidden) ordemHidden.value = getOrdemAtual();
                const dirSalva = localStorage.getItem(ordemDirStorageKey);
                if (dirSalva === 'recentes' || dirSalva === 'paradas') dirAtividade = dirSalva;
                atualizarDirToggleUI();
                atualizarBadgeOrdem();
                ordenarCards();
            }

            function getStatusAtivos() {
                const ativo = new Set();
                if (todasCheckbox && todasCheckbox.checked) { ativo.add('todas'); return ativo; }
                statusCheckboxes.forEach(cb => { if (cb && cb.checked) ativo.add(cb.value); });
                if (ativo.size === 0) ativo.add('todas');
                return ativo;
            }

            function getTiposAtivos() {
                const ativo = new Set();
                if (tipoTodasCheckbox && tipoTodasCheckbox.checked) { ativo.add('todas'); return ativo; }
                tipoCheckboxes.forEach(cb => { if (cb && cb.checked) ativo.add(cb.value); });
                if (ativo.size === 0) ativo.add('todas');
                return ativo;
            }

            function getEstadosAtivos() {
                const ativo = new Set();
                if (estadoTodasCheckbox && estadoTodasCheckbox.checked) { ativo.add('todas'); return ativo; }
                estadoCheckboxes.forEach(cb => { if (cb && cb.checked) ativo.add(cb.value); });
                if (ativo.size === 0) ativo.add('todas');
                return ativo;
            }

            function getCidadesAtivas() {
                const ativo = new Set();
                if (cidadeTodasCheckbox && cidadeTodasCheckbox.checked) { ativo.add('todas'); return ativo; }
                cidadeCheckboxes.forEach(cb => { if (cb && cb.checked) ativo.add(cb.value); });
                if (ativo.size === 0) ativo.add('todas');
                return ativo;
            }

            // Recorte "cidade segue o estado": esconde os chips de cidade que não
            // pertencem aos estados marcados (e desmarca os que ficarem ocultos).
            function aplicarHierarquiaCidade() {
                const estadosAtivos = getEstadosAtivos();
                const semFiltroEstado = estadosAtivos.has('todas');
                document.querySelectorAll('.dd-cidade-item').forEach(item => {
                    const estadosCidade = (item.dataset.estados || '').split(',').filter(Boolean);
                    const pertence = semFiltroEstado || estadosCidade.some(uf => estadosAtivos.has(uf));
                    item.classList.toggle('dd-hidden', !pertence);
                    if (!pertence) {
                        const cb = item.querySelector('.btn-check');
                        if (cb && cb.checked) cb.checked = false;
                    }
                });
                const algumaMarcada = cidadeCheckboxes.some(cb => cb && cb.checked);
                if (!algumaMarcada && cidadeTodasCheckbox) cidadeTodasCheckbox.checked = true;
            }

            function aplicarFiltros() {
                const statusAtivos = getStatusAtivos();
                const tiposAtivos = getTiposAtivos();
                const estadosAtivos = getEstadosAtivos();
                const cidadesAtivas = getCidadesAtivas();
                const lista = document.getElementById('lista-empresas');
                const semResultados = document.getElementById('sem-resultados');
                let visiveis = 0;

                document.querySelectorAll('.company-card').forEach(card => {
                    if (card.dataset.searchHidden === '1') return;
                    // estado e cidade são por empresa: filtram o card inteiro
                    const estadoCardOk = estadosAtivos.has('todas') || estadosAtivos.has(card.dataset.estadoEmpresa);
                    const cidadeCardOk = cidadesAtivas.has('todas') || cidadesAtivas.has(card.dataset.cidadeKey);
                    if (!estadoCardOk || !cidadeCardOk) {
                        card.classList.add('status-hidden');
                        return;
                    }
                    let linhasVisiveis = 0;
                    card.querySelectorAll('tr[data-tipo]').forEach(row => {
                        const tipoOk = tiposAtivos.has('todas') || tiposAtivos.has(row.dataset.tipo);
                        const statusOk = statusAtivos.has('todas') || statusAtivos.has(row.dataset.statusCert);
                        const visivel = tipoOk && statusOk;
                        row.classList.toggle('filter-hidden', !visivel);
                        if (visivel) linhasVisiveis++;
                    });
                    const cardVisivel = linhasVisiveis > 0;
                    card.classList.toggle('status-hidden', !cardVisivel);
                    if (cardVisivel) visiveis++;
                });

                if (semResultados) semResultados.style.display = visiveis === 0 ? '' : 'none';

                const url = new URL(window.location);
                url.searchParams.delete('status');
                url.searchParams.delete('tipo');
                url.searchParams.delete('estado');
                url.searchParams.delete('cidade');
                if (!statusAtivos.has('todas')) statusAtivos.forEach(s => url.searchParams.append('status', s));
                if (!tiposAtivos.has('todas')) tiposAtivos.forEach(t => url.searchParams.append('tipo', t));
                if (!estadosAtivos.has('todas')) estadosAtivos.forEach(e => url.searchParams.append('estado', e));
                if (!cidadesAtivas.has('todas')) cidadesAtivas.forEach(c => url.searchParams.append('cidade', c));
                history.replaceState(null, '', url);

                atualizarContagensChips();
            }

            function compararUrgencia(a, b, nomeA, nomeB) {
                // Gravidade + volume em cascata: a comparação lexicográfica por essas
                // contagens (desc) reproduz os degraus de gravidade — quem tem qualquer
                // vencida vence quem tem 0 — e, dentro de cada nível, quem tem MAIS
                // pendências sobe. Empresas de mesmo perfil desempatam por nome.
                const chaves = ['countVencidas', 'countAVencer', 'countPendentes', 'countNaoDefinida'];
                for (const k of chaves) {
                    const diff = Number(b.dataset[k] || 0) - Number(a.dataset[k] || 0);
                    if (diff !== 0) return diff;
                }
                return nomeA.localeCompare(nomeB);
            }

            function ordenarCards() {
                const lista = document.getElementById('lista-empresas');
                if (!lista) return;
                const ordem = getOrdemAtual();
                const cards = Array.from(lista.querySelectorAll('.company-card'));
                cards.sort((a, b) => {
                    const nomeA = (a.dataset.nomeEmpresa || '').toUpperCase();
                    const nomeB = (b.dataset.nomeEmpresa || '').toUpperCase();
                    if (ordem === 'az') return nomeA.localeCompare(nomeB);
                    if (ordem === 'vencimento') {
                        const valA = a.dataset.menorValidade || '9999-12-31';
                        const valB = b.dataset.menorValidade || '9999-12-31';
                        if (valA !== valB) return valA.localeCompare(valB);
                        return nomeA.localeCompare(nomeB);
                    }
                    if (ordem === 'atualizacao') {
                        // 'paradas' (padrao): ISO ascendente — parada ha mais tempo no topo;
                        // sem dado ('') vem antes de qualquer ISO (topo). 'recentes': inverte
                        // (desc) — atualizada agora no topo, sem dado cai pro fim. Nome sempre
                        // desempata A->Z, independente da direcao.
                        const atA = a.dataset.ultimaAtualizacao || '';
                        const atB = b.dataset.ultimaAtualizacao || '';
                        if (atA !== atB) {
                            const cmp = atA.localeCompare(atB);
                            return dirAtividade === 'recentes' ? -cmp : cmp;
                        }
                        return nomeA.localeCompare(nomeB);
                    }
                    return compararUrgencia(a, b, nomeA, nomeB);
                });
                cards.forEach(card => lista.appendChild(card));
            }

            function atualizarContagensChips() {
                const statusChips = document.querySelectorAll('.chip-count[data-status]');
                const typeChips = document.querySelectorAll('.chip-count[data-type]');
                const estadoChips = document.querySelectorAll('.chip-count[data-estado]');
                const cidadeChips = document.querySelectorAll('.chip-count[data-cidade]');
                const statusAtivos = getStatusAtivos();
                const tiposAtivos = getTiposAtivos();
                const estadosAtivos = getEstadosAtivos();
                const cidadesAtivas = getCidadesAtivas();

                const statusTotals = { todas: 0, validas: 0, a_vencer: 0, vencidas: 0, pendentes: 0, nao_definida: 0 };
                const typeTotals = { todas: 0, federal: 0, fgts: 0, estadual: 0, municipal: 0, trabalhista: 0 };
                const estadoTotals = { todas: 0 };
                const cidadeTotals = { todas: 0 };

                const casa = (ativos, v) => ativos.has('todas') || ativos.has(v);

                document.querySelectorAll('.company-card:not(.search-hidden) tr[data-tipo]').forEach(row => {
                    const tipo = row.dataset.tipo;
                    const status = row.dataset.statusCert;
                    const estado = row.dataset.estado;
                    const cidade = row.dataset.cidadeKey;

                    const okStatus = casa(statusAtivos, status);
                    const okTipo = casa(tiposAtivos, tipo);
                    const okEstado = casa(estadosAtivos, estado);
                    const okCidade = casa(cidadesAtivas, cidade);

                    // cada dimensão conta as linhas que casam com as OUTRAS três (combinável)
                    if (okTipo && okEstado && okCidade) {
                        statusTotals.todas++;
                        if (Object.prototype.hasOwnProperty.call(statusTotals, status)) statusTotals[status]++;
                    }
                    if (okStatus && okEstado && okCidade) {
                        typeTotals.todas++;
                        if (Object.prototype.hasOwnProperty.call(typeTotals, tipo)) typeTotals[tipo]++;
                    }
                    if (okStatus && okTipo && okCidade) {
                        estadoTotals.todas++;
                        estadoTotals[estado] = (estadoTotals[estado] || 0) + 1;
                    }
                    if (okStatus && okTipo && okEstado) {
                        cidadeTotals.todas++;
                        cidadeTotals[cidade] = (cidadeTotals[cidade] || 0) + 1;
                    }
                });

                const aplicarContagem = (chips, totals) => {
                    chips.forEach(chip => {
                        const k = chip.dataset.status || chip.dataset.type || chip.dataset.estado || chip.dataset.cidade;
                        const val = Object.prototype.hasOwnProperty.call(totals, k) ? totals[k] : 0;
                        chip.textContent = val;
                        const host = chip.closest('.filter-chip, .resumo-item');
                        if (host) host.classList.toggle('chip-empty', val === 0);
                    });
                };
                aplicarContagem(statusChips, statusTotals);
                aplicarContagem(typeChips, typeTotals);
                aplicarContagem(estadoChips, estadoTotals);
                aplicarContagem(cidadeChips, cidadeTotals);

                // badge dos dropdowns: rótulo "Todos/Todas" ou a quantidade de selecionados
                atualizarBadge('tipo-dd-badge', tipoCheckboxes, 'Todas');
                atualizarBadge('estado-dd-badge', estadoCheckboxes, 'Todos');
                atualizarBadge('cidade-dd-badge', cidadeCheckboxes, 'Todas');
            }

            function atualizarBadge(id, checkboxes, rotuloTodas) {
                const badge = document.getElementById(id);
                if (!badge) return;
                const ativos = checkboxes.filter(cb => cb && cb.checked).length;
                badge.textContent = ativos === 0 ? rotuloTodas : String(ativos);
            }

            function setupChipGroup(todasCb, outrasCbs, afterToggle) {
                if (todasCb) {
                    todasCb.addEventListener('change', function () {
                        if (todasCb.checked) {
                            outrasCbs.forEach(cb => { if (cb) cb.checked = false; });
                        } else {
                            const algumMarcado = outrasCbs.some(cb => cb && cb.checked);
                            if (!algumMarcado) todasCb.checked = true;
                        }
                        if (afterToggle) afterToggle();
                        aplicarFiltros();
                    });
                }

                outrasCbs.forEach(cb => {
                    if (cb) {
                        cb.addEventListener('change', function () {
                            if (cb.checked) {
                                if (todasCb) todasCb.checked = false;
                            } else {
                                const algumMarcado = outrasCbs.some(other => other && other.checked);
                                if (!algumMarcado && todasCb) todasCb.checked = true;
                            }
                            if (afterToggle) afterToggle();
                            aplicarFiltros();
                        });
                    }
                });
            }

            setupChipGroup(todasCheckbox, statusCheckboxes);
            setupChipGroup(tipoTodasCheckbox, tipoCheckboxes);
            // ao mexer no estado, reaplica o recorte "cidade segue o estado" antes de filtrar
            setupChipGroup(estadoTodasCheckbox, estadoCheckboxes, aplicarHierarquiaCidade);
            setupChipGroup(cidadeTodasCheckbox, cidadeCheckboxes);

            ordemRadios.forEach(radio => {
                radio.addEventListener('change', function () {
                    if (!radio.checked) return;
                    localStorage.setItem(ordemStorageKey, radio.value);
                    if (ordemHidden) ordemHidden.value = radio.value;
                    atualizarDirToggleUI();
                    atualizarBadgeOrdem();
                    ordenarCards();
                    fecharTodosDropdowns();
                });
            });

            if (dirToggle) {
                dirToggle.addEventListener('click', function () {
                    dirAtividade = dirAtividade === 'recentes' ? 'paradas' : 'recentes';
                    localStorage.setItem(ordemDirStorageKey, dirAtividade);
                    atualizarDirToggleUI();
                    atualizarBadgeOrdem();
                    ordenarCards();
                });
            }

            // Dropdowns de chips (Estado/Cidade/Tipo): abre no toggle (fechando os demais),
            // fecha ao clicar fora ou Esc; clicar nos chips dentro não fecha.
            function fecharTodosDropdowns() {
                document.querySelectorAll('.filtro-dd').forEach(o => {
                    o.classList.remove('open');
                    const t = o.querySelector('.filtro-dd-toggle');
                    if (t) t.setAttribute('aria-expanded', 'false');
                });
            }
            document.querySelectorAll('.filtro-dd').forEach(dd => {
                const toggle = dd.querySelector('.filtro-dd-toggle');
                if (!toggle) return;
                toggle.addEventListener('click', function (e) {
                    e.stopPropagation();
                    const aberto = dd.classList.contains('open');
                    fecharTodosDropdowns();
                    if (!aberto) {
                        dd.classList.add('open');
                        toggle.setAttribute('aria-expanded', 'true');
                    }
                });
            });
            document.addEventListener('click', function (e) {
                if (!e.target.closest('.filtro-dd')) fecharTodosDropdowns();
            });
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') fecharTodosDropdowns();
            });

            aplicarOrdenacaoSalva();
            aplicarHierarquiaCidade();
            aplicarFiltros();

            // Revela lista, contagens dos chips e remove skeleton após inicialização completa
            (function () {
                const skEl = document.getElementById('skeleton-dashboard');
                const listaEl = document.getElementById('lista-empresas');
                const filtrosEl = document.getElementById('filtros-card');
                if (skEl) skEl.style.display = 'none';
                if (listaEl) listaEl.classList.remove('dashboard-loading');
                if (filtrosEl) filtrosEl.classList.remove('chips-loading');
            })();

            window.addEventListener('focus', function () {
                cleanupUiLocks({ keepLoading: true, keepBatchOverlays: true });
            });
        });


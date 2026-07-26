"""Rotas de certidao: atualizar/validar, marcar pendente, baixar (fina),
salvar data confirmada, monitor de download federal, visualizacao por token.

Extraido de app/routes.py (spec 05, REFA-02). Registra no blueprint "main"
compartilhado (importado de app.routes). A rota baixar delega a emissao_service.
"""
import os
import tempfile
import time
from datetime import datetime

from flask import (
    flash,
    jsonify,
    redirect,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from app import db, file_manager
from app.automation.emissao import (
    _classe_status_por_data,
    _pick_changed_download_pdf,
    _snapshot_downloads_pdf,
)
from app.models import (
    Certidao,
    TipoCertidao,
)
from app.utils import (
    json_error as _json_error,
)
from app.services import (
    auditoria,
    certidao_service,
    emissao_service,
)
from app.services.execution_logger import log_event
from app.services.visualizar_token import (
    _carregar_visualizar_token,
    _gerar_visualizar_token,
)
from app.auth import requer_papel

from app.routes import bp


@bp.route('/certidao/atualizar/<int:certidao_id>', methods=['POST'])
@requer_papel('operador')
def atualizar_validade(certidao_id):
    certidao = Certidao.query.get_or_404(certidao_id)
    nova_data_str = request.form.get('nova_validade')

    if nova_data_str:
        nova_data = datetime.strptime(nova_data_str, '%Y-%m-%d').date()
        ok, erro = certidao_service.aplicar_validade(certidao, nova_data)
        if ok:
            flash(
                f"Validade da certidão {certidao.tipo.value} da empresa {certidao.empresa.nome} atualizada com sucesso!", 'success')
            auditoria.registrar('certidao.aplicar_validade', alvo_tipo='certidao', alvo_id=certidao_id)
        else:
            flash(f"Erro ao atualizar validade: {erro}", 'danger')
            auditoria.registrar('certidao.aplicar_validade', alvo_tipo='certidao',
                                alvo_id=certidao_id, resultado='erro', detalhe=erro)
    else:
        flash("Nenhuma data foi fornecida.", 'warning')
    return redirect(url_for('main.dashboard'))


@bp.route('/certidao/marcar_pendente/<int:certidao_id>', methods=['POST'])
@requer_papel('operador')
def marcar_pendente(certidao_id):
    certidao = Certidao.query.get_or_404(certidao_id)
    ok, erro = certidao_service.marcar_pendente(certidao)
    if ok:
        flash(
            f'Certidão {certidao.tipo.value} da empresa {certidao.empresa.nome} marcada como Pendente.', 'info')
        auditoria.registrar('certidao.marcar_pendente', alvo_tipo='certidao', alvo_id=certidao_id)
    else:
        flash(f'Erro ao marcar como pendente: {erro}', 'danger')
        auditoria.registrar('certidao.marcar_pendente', alvo_tipo='certidao',
                            alvo_id=certidao_id, resultado='erro', detalhe=erro)

    return redirect(url_for('main.dashboard'))


@bp.route('/certidao/baixar/<int:certidao_id>')
@requer_papel('operador')
def baixar_certidao(certidao_id):
    return emissao_service.baixar_certidao(certidao_id)


@bp.route('/certidao/salvar_data_confirmada', methods=['POST'])
@requer_papel('operador')
def salvar_data_confirmada():
    dados = request.get_json()
    certidao_id = dados.get('certidao_id')
    nova_validade_str = dados.get('nova_validade')

    try:
        certidao = db.session.get(Certidao, certidao_id)
        nova_data = datetime.strptime(nova_validade_str, '%Y-%m-%d').date()

        ok, erro = certidao_service.aplicar_validade(certidao, nova_data)
        if not ok:
            return _json_error(erro, 500)

        return jsonify({
            'status': 'success',
            'message': 'Data confirmada e atualizada com sucesso!',
            'nova_data_formatada': nova_data.strftime('%d/%m/%Y'),
            'nova_classe': _classe_status_por_data(nova_data, tipo=certidao.tipo)
        })
    except Exception as e:
        return _json_error(code=500, exc=e)


@bp.route('/certidao/monitorar_download_federal/<int:certidao_id>')
@requer_papel('operador')
def monitorar_download_federal(certidao_id):
    certidao = Certidao.query.get_or_404(certidao_id)

    log_event('federal_monitor_start', certidao_id=certidao_id)

    minha_chave_ts = file_manager.criar_chave_interrupcao()

    # Captura um snapshot antes de iniciar a janela de monitoramento
    # para detectar arquivos criados/alterados mesmo se o download iniciar cedo.
    snapshot_before = _snapshot_downloads_pdf()
    log_event('federal_monitor_snapshot', certidao_id=certidao_id, pdfs=len(snapshot_before))

    time.sleep(2)

    # Se a chave foi recriada durante o sleep (por /stop ou nova sessão), sair.
    if file_manager.chave_interrupcao_mais_recente_que(minha_chave_ts):
        file_manager.remover_chave_interrupcao()
        return _json_error('Monitoramento interrompido antes de iniciar.', 409, status='interrupted')

    file_manager.remover_chave_interrupcao()

    tempo_limite = 180
    tempo_inicio = time.time()
    chave_interrupcao = file_manager.obter_caminho_chave_interrupcao()
    ultimo_log = tempo_inicio

    termos_proibidos = [
        'consulta regularidade',
        'crf',
        'cndt',
        'sitafe'
    ]

    while (time.time() - tempo_inicio) < tempo_limite:
        if os.path.exists(chave_interrupcao):
            log_event(
                'federal_monitor_interrupted', level='WARNING', certidao_id=certidao_id,
                message='Monitoramento interrompido por nova requisição.',
            )
            file_manager.remover_chave_interrupcao()
            return _json_error('Monitoramento interrompido.', 409, status='interrupted')

        novo_arquivo = _pick_changed_download_pdf(snapshot_before)
        if not novo_arquivo:
            novo_arquivo = file_manager.verificar_novo_arquivo(
                tempo_inicio, termos_ignorar=termos_proibidos)

        agora = time.time()
        if (agora - ultimo_log) >= 5:
            restante = max(0, int(tempo_limite - (agora - tempo_inicio)))
            log_event(
                'federal_monitor_waiting', certidao_id=certidao_id,
                restante_s=restante, novo_arquivo=bool(novo_arquivo),
            )
            ultimo_log = agora

        if novo_arquivo:
            log_event('federal_file_detected', certidao_id=certidao_id, arquivo=str(novo_arquivo))

            sucesso, msg = file_manager.mover_e_renomear(
                novo_arquivo,
                certidao.empresa.nome,
                certidao.tipo.value
            )

            if sucesso:
                try:
                    certidao.caminho_arquivo = msg
                    db.session.commit()
                except Exception as e_db:
                    db.session.rollback()
                    log_event(
                        'federal_db_save_failed', level='WARNING',
                        certidao_id=certidao_id, error=str(e_db),
                    )

                # Nucleo compartilhado (COV-01b): classifica (positiva->PENDENTE) e
                # decide a validade (data do PDF ou fallback 180d). Mesmo helper do
                # upload manual (F3) — sem duplicar classificacao/validade.
                res = certidao_service.finalizar_federal(certidao, msg)
                if not res['ok']:
                    return _json_error(res.get('message') or 'Falha ao registrar a Federal.', 500)

                if res.get('pendente'):
                    return jsonify({
                        'status': 'pendente',
                        'mensagem': res.get('message')
                        or 'Certidão Federal positiva: marcada como pendente.',
                    })

                validade = res.get('data_validade')
                payload = {
                    'status': 'success',
                    'mensagem': f"Arquivo salvo no servidor: {msg}",
                    'visualizar_token': _gerar_visualizar_token(certidao_id),
                }
                if validade:
                    payload['data_validade'] = validade.strftime('%Y-%m-%d')
                    payload['data_validade_formatada'] = validade.strftime('%d/%m/%Y')
                return jsonify(payload)
            else:
                return _json_error(f"Erro ao mover: {msg}", 500)

        time.sleep(1)

    # limpeza final por segurança
    file_manager.remover_chave_interrupcao()
    return _json_error('Tempo esgotado sem download.', 408, status='timeout')


@bp.route('/certidao/monitorar_download_federal/stop', methods=['POST'])
@requer_papel('operador')
def interromper_monitoramento_federal():
    file_manager.criar_chave_interrupcao()
    return jsonify({'status': 'ok'})


# Limite generoso: certidoes federais em PDF tem ~100 KB; 15 MB cobre com folga.
_MAX_UPLOAD_FEDERAL_BYTES = 15 * 1024 * 1024


@bp.route('/certidao/federal/registrar/<int:certidao_id>', methods=['POST'])
@requer_papel('operador')
def registrar_federal_upload(certidao_id):
    """Registra uma certidao FEDERAL por upload manual (COV-01b F3).

    Alternativa ao 'Abrir Site' + monitor: o operador anexa um PDF que ja tem em
    maos. Salva o arquivo e aplica a MESMA finalizacao do monitor
    (`certidao_service.finalizar_federal`) — classificacao + validade/180d — sem
    duplicar. So o tipo FEDERAL; papel operador (AD-005)."""
    certidao = db.session.get(Certidao, certidao_id)
    if not certidao:
        return _json_error('Certidão não encontrada.', 404)
    if certidao.tipo != TipoCertidao.FEDERAL:
        return _json_error('Registro por upload é exclusivo do tipo Federal.', 400)

    arquivo = request.files.get('arquivo')
    if arquivo is None or not (arquivo.filename or '').strip():
        return _json_error('Nenhum arquivo enviado.', 400)
    if not secure_filename(arquivo.filename).lower().endswith('.pdf'):
        return _json_error('Envie um arquivo PDF.', 400)

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        arquivo.save(temp_path)

        tamanho = os.path.getsize(temp_path)
        if tamanho == 0:
            return _json_error('Arquivo vazio.', 400)
        if tamanho > _MAX_UPLOAD_FEDERAL_BYTES:
            return _json_error('Arquivo excede o limite de 15 MB.', 413)

        sucesso, destino = file_manager.mover_e_renomear(
            temp_path, certidao.empresa.nome, certidao.tipo.value)
        if not sucesso:
            return _json_error(f'Erro ao salvar o arquivo: {destino}', 500)
        temp_path = None  # movido para a pasta da empresa; nao remover no finally

        certidao.caminho_arquivo = destino
        db.session.commit()
        log_event('federal_upload_registrado', certidao_id=certidao_id, arquivo=destino)

        res = certidao_service.finalizar_federal(certidao, destino)
        if not res['ok']:
            return _json_error(res.get('message') or 'Falha ao registrar a Federal.', 500)

        if res.get('pendente'):
            return jsonify({
                'status': 'pendente',
                'mensagem': res.get('message')
                or 'Certidão Federal positiva: marcada como pendente.',
            })

        validade = res.get('data_validade')
        payload = {
            'status': 'success',
            'mensagem': f'Certidão Federal registrada: {destino}',
            'visualizar_token': _gerar_visualizar_token(certidao_id),
        }
        if validade:
            payload['data_validade'] = validade.strftime('%Y-%m-%d')
            payload['data_validade_formatada'] = validade.strftime('%d/%m/%Y')
        return jsonify(payload)
    except Exception as e:
        db.session.rollback()
        return _json_error(code=500, exc=e)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


@bp.route('/certidao/visualizar/<token>')
def visualizar_certidao(token):
    certidao_id = _carregar_visualizar_token(token)
    if not certidao_id:
        return 'Token inválido ou expirado.', 404

    certidao = Certidao.query.get_or_404(certidao_id)
    caminho = certidao.caminho_arquivo

    if not caminho or not os.path.exists(caminho):
        caminho = file_manager.localizar_certidao_existente(
            certidao.empresa.nome,
            certidao.tipo.value,
            certidao.subtipo.value if certidao.subtipo else None
        )
        if caminho:
            certidao.caminho_arquivo = caminho
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    if not caminho or not os.path.exists(caminho):
        return 'Arquivo não encontrado para esta certidão.', 404

    return send_file(
        caminho,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=os.path.basename(caminho)
    )


@bp.route('/certidao/<int:certidao_id>/token-visualizar')
def gerar_token_visualizar(certidao_id):
    """Gera token de visualização sob demanda (lazy), evitando crypto no render do dashboard."""
    certidao = Certidao.query.get_or_404(certidao_id)
    caminho = certidao.caminho_arquivo

    if not caminho or not os.path.exists(caminho):
        caminho = file_manager.localizar_certidao_existente(
            certidao.empresa.nome,
            certidao.tipo.value,
            certidao.subtipo.value if certidao.subtipo else None
        )
        if caminho:
            certidao.caminho_arquivo = caminho
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    if not caminho or not os.path.exists(caminho):
        return jsonify({'erro': 'sem_arquivo'}), 404

    token = _gerar_visualizar_token(certidao_id)
    return jsonify({'url': url_for('main.visualizar_certidao', token=token)})


@bp.route('/certidao/marcar_pendente_json/<int:certidao_id>', methods=['POST'])
@requer_papel('operador')
def marcar_pendente_json(certidao_id):
    try:
        certidao = Certidao.query.get_or_404(certidao_id)
        ok, erro = certidao_service.marcar_pendente(certidao)
        if not ok:
            auditoria.registrar('certidao.marcar_pendente', alvo_tipo='certidao',
                                alvo_id=certidao_id, resultado='erro', detalhe=erro)
            return _json_error(erro, 500)
        auditoria.registrar('certidao.marcar_pendente', alvo_tipo='certidao', alvo_id=certidao_id)
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        auditoria.registrar('certidao.marcar_pendente', alvo_tipo='certidao',
                            alvo_id=certidao_id, resultado='erro', detalhe=str(e))
        return _json_error(code=500, exc=e)


@bp.route('/certidao/atualizar_json/<int:certidao_id>', methods=['POST'])
@requer_papel('operador')
def atualizar_validade_json(certidao_id):
    data = request.get_json()
    nova_data_str = data.get('nova_validade')

    try:
        certidao = Certidao.query.get_or_404(certidao_id)

        if nova_data_str:
            nova_data = datetime.strptime(nova_data_str, '%Y-%m-%d').date()
            ok, erro = certidao_service.aplicar_validade(certidao, nova_data)
            if not ok:
                auditoria.registrar('certidao.aplicar_validade', alvo_tipo='certidao',
                                    alvo_id=certidao_id, resultado='erro', detalhe=erro)
                return _json_error(erro, 500)

            auditoria.registrar('certidao.aplicar_validade', alvo_tipo='certidao', alvo_id=certidao_id)
            return jsonify({
                'status': 'success',
                'message': f'Validade de {certidao.empresa.nome} atualizada com sucesso!',
                'nova_data_formatada': nova_data.strftime('%d/%m/%Y'),
                'nova_classe': _classe_status_por_data(nova_data, tipo=certidao.tipo)
            })
        else:
            return _json_error('Data inválida.', 400)

    except Exception as e:
        db.session.rollback()
        return _json_error(code=500, exc=e)


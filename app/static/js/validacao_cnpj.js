// @ts-check

/**
 * Confere os dois dígitos verificadores de um CNPJ sem formatação.
 *
 * @param {string} cnpj CNPJ com exatamente 14 dígitos.
 * @returns {boolean} Verdadeiro quando os dígitos conferem.
 */
export function digito_verificador_ok(cnpj) {
  if (cnpj.length !== 14 || /^(\d)\1{13}$/.test(cnpj)) return false;
  /** @param {string} base */
  const calcula = (base) => {
    let peso = base.length - 7;
    const soma = [...base].reduce((acumulado, digito) => {
      acumulado += Number(digito) * peso--;
      if (peso < 2) peso = 9;
      return acumulado;
    }, 0);
    const resto = soma % 11;
    return resto < 2 ? 0 : 11 - resto;
  };
  const primeiro = calcula(cnpj.slice(0, 12));
  const segundo = calcula(cnpj.slice(0, 12) + primeiro);
  return cnpj === cnpj.slice(0, 12) + String(primeiro) + String(segundo);
}

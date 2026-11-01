// Página que consume la API: /api/salud, /api/modelo y /api/diagnostico (mismo origen, sin CORS).
const $ = (id) => document.getElementById(id);
const porcentaje = (x) => `${(x * 100).toFixed(1)} %`;
let archivo = null;

async function revisarApi() {
  const estado = $("estado-api");
  try {
    const r = await fetch("/api/salud");
    const datos = await r.json();
    estado.textContent = `API conectada · ${datos.modelo}`;
    estado.className = "estado ok";
  } catch {
    estado.textContent = "API sin conexión";
    estado.className = "estado falla";
  }
}

async function cargarModelo() {
  const r = await fetch("/api/modelo");
  const m = await r.json();
  const filas = [
    ["Modelo", `${m.nombre} v${m.version_registrada} (${m.algoritmo})`],
    ["Características", `MobileNetV2 congelado, ${m.extractor.salida}`],
    ["Clases", m.clases.join(", ")],
    ["Umbral", `enferma si P(enferma) ≥ ${m.umbral_en_uso}`],
    ["Prueba", `${m.metricas_prueba.n_fotos} fotos de parcela: F1 macro ${m.metricas_prueba.f1_macro.toFixed(2)}, ` +
               `enfermas reportadas como sanas ${porcentaje(m.metricas_prueba.con_umbral_enferma_como_sana)} con el umbral`],
  ];
  $("modelo").innerHTML = filas.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function elegir(f) {
  if (!f) return;
  archivo = f;
  $("vista").src = URL.createObjectURL(f);
  $("vista").hidden = false;
  $("zona-texto").hidden = true;
  $("enviar").disabled = false;
}

async function diagnosticar() {
  const boton = $("enviar");
  boton.disabled = true;
  boton.textContent = "Analizando…";
  $("error").hidden = true;
  const formulario = new FormData();
  formulario.append("foto", archivo);
  try {
    const r = await fetch("/api/diagnostico", { method: "POST", body: formulario });
    const datos = await r.json();
    if (!r.ok) throw new Error(datos.detail || `error ${r.status}`);
    mostrar(datos);
  } catch (e) {
    $("resultado").hidden = true;
    $("error").textContent = `No se pudo diagnosticar: ${e.message}`;
    $("error").hidden = false;
  } finally {
    boton.disabled = false;
    boton.textContent = "Diagnosticar";
  }
}

function mostrar(d) {
  $("sin-resultado").hidden = true;
  $("resultado").hidden = false;
  $("r-estado").textContent = d.estado;
  $("r-estado").className = `etiqueta ${d.estado}`;
  $("r-clase").textContent = d.diagnostico;
  $("r-penferma").textContent = porcentaje(d.probabilidad_enferma);
  $("r-barra-enferma").style.width = porcentaje(d.probabilidad_enferma).replace(" ", "");
  $("r-marca-umbral").style.left = porcentaje(d.umbral_enferma).replace(" ", "");
  $("r-umbral").textContent = `La línea negra es el umbral (${porcentaje(d.umbral_enferma)}): desde ahí se reporta enferma.`;
  $("r-aviso").hidden = !d.decidido_por_umbral;
  $("r-aviso").textContent = `La clase más probable es Sana, pero la probabilidad de enfermedad pasa el umbral, ` +
    `así que se reporta como ${d.diagnostico}. Conviene revisar la hoja.`;
  $("r-clases").innerHTML = Object.entries(d.probabilidades).map(([clase, p]) =>
    `<li><span>${clase}</span><div class="pista"><div style="width:${(p * 100).toFixed(1)}%"></div></div>` +
    `<span class="valor">${porcentaje(p)}</span></li>`).join("");
  $("r-tiempo").textContent = `Foto de ${d.imagen.ancho}×${d.imagen.alto} (${d.imagen.formato}), ` +
    `analizada en ${d.tiempo_ms} ms.`;
  $("r-json").textContent = JSON.stringify(d, null, 2);
}

const zona = $("zona");
$("foto").addEventListener("change", (e) => elegir(e.target.files[0]));
zona.addEventListener("dragover", (e) => { e.preventDefault(); zona.classList.add("encima"); });
zona.addEventListener("dragleave", () => zona.classList.remove("encima"));
zona.addEventListener("drop", (e) => {
  e.preventDefault();
  zona.classList.remove("encima");
  elegir(e.dataTransfer.files[0]);
});
$("enviar").addEventListener("click", diagnosticar);

revisarApi();
cargarModelo();

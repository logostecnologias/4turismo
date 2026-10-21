"""Os textos fixos do site público em português, inglês e espanhol.

O que a agência escreve (descrição, roteiro, perguntas, guias) é traduzido por
`site_traducao`; aqui fica só o que é do modelo do site: títulos de seção,
botões, rótulos e a mensagem pronta do WhatsApp.

O espanhol é o neutro latino-americano: quem chega aos Lençóis falando espanhol
vem sobretudo da Argentina, do Chile e do Uruguai, não da Espanha.
"""
from datetime import date

PADRAO = "pt"
IDIOMAS = ("pt", "en", "es")
EXTRAS = ("en", "es")             # os que a agência liga ou desliga
NOMES = {"pt": "Português", "en": "English", "es": "Español"}       # no seletor, cada um na sua língua
EM_PORTUGUES = {"pt": "português", "en": "inglês", "es": "espanhol"}  # para quem atende
CURTOS = {"pt": "PT", "en": "EN", "es": "ES"}
HTML_LANG = {"pt": "pt-BR", "en": "en", "es": "es"}
OG_LOCALE = {"pt": "pt_BR", "en": "en_US", "es": "es_LA"}
NOME_PARA_IA = {"en": "inglês (dos Estados Unidos)", "es": "espanhol neutro da América Latina"}

DIAS_CURTOS = {
    "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}
DIAS_LONGOS = {
    "pt": ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
}
MESES_CURTOS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}

T = {
    "pt": {
        "passeios": "Passeios", "passeios_sub": "Escolha o passeio, veja as datas e fale com a gente pelo WhatsApp.",
        "ver_passeios": "Ver passeios", "falar_whatsapp": "Falar no WhatsApp", "fale_conosco": "Fale conosco",
        "fale_conosco_aria": "Fale conosco pelo WhatsApp",
        "por_pessoa": "por pessoa", "por_adulto": "por adulto", "crianca": "Criança", "gratis": "grátis",
        "ver_detalhes": "Ver detalhes e datas →", "em_breve": "Em breve, os nossos passeios aqui. Enquanto isso, fale com a gente pelo WhatsApp.",
        "por_que": "Por que viajar com a gente", "ha_anos": "Há {n} anos recebendo turistas na região.",
        "cadastur": "Agência registrada no CADASTUR · {n}", "desde": "Desde {n}",
        "quem_ja_foi": "Quem já foi", "ver_avaliacoes": "Ver todas as avaliações no {fonte} →",
        "avaliacoes_no": "avaliações no", "nota_de": "Nota {nota} de 5 no {fonte}", "no": "no",
        "busca_titulo": "Buscamos você na hospedagem",
        "busca_sub": "Nos passeios, a busca sai destes pontos. A hora certa vai na confirmação da reserva.",
        "busca_antes": "Busca cerca de {n} min antes da saída", "busca_hora": "Busca no horário da saída",
        "busca_outro": "Está hospedado em outro lugar? Fale com a gente e combinamos a busca.",
        "perguntas": "Perguntas frequentes", "perguntas_passeio": "Perguntas sobre este passeio",
        "mais_perguntas": "Mais perguntas frequentes →",
        "sobre": "Sobre nós", "onde_estamos": "Onde estamos", "atendimento": "Atendimento",
        "ver_mapa": "Ver no mapa →", "pagamento": "Formas de pagamento", "pagamento_curto": "Pagamento",
        "cancelamento": "Cancelamento",
        "todos_passeios": "← Todos os passeios", "como_e_o_dia": "Como é o dia",
        "inclui": "O que inclui", "levar": "O que levar", "bom_saber": "Bom saber",
        "melhor_epoca": "Melhor época", "se_chover": "Se chover",
        "esforco": {"leve": "Esforço leve", "moderado": "Esforço moderado", "intenso": "Esforço intenso"},
        "a_partir_de": "A partir de {n} anos", "todas_idades": "Todas as idades",
        "consulte_valor": "Consulte o valor", "data": "Data", "ainda_nao_sei": "Ainda não sei",
        "sem_datas": "Sem datas abertas nas próximas semanas. Fale com a gente para saber das próximas.",
        "adultos": "Adultos", "criancas": "Crianças", "quero": "Quero esse passeio",
        "nota_reserva": "Você continua a conversa no WhatsApp. A reserva é confirmada pela nossa equipe.",
        "escolher_data": "Escolher data",
        "todos_os_dias": "Todos os dias", "a": "a", "e": "e",
        "guias": "Guias da região", "guias_sub": "O que saber antes de vir.", "ver_guia": "Ler o guia →",
        "todos_guias": "← Todos os guias", "guias_relacionados": "Para ler antes de ir",
        "passeios_relacionados": "Passeios para fazer",
        "previa": "Pré-visualização: este site ainda não está publicado e só você está vendo.",
        "nao_encontrada": "Página não encontrada", "nao_encontrada_sub": "O endereço pode ter mudado.",
        "como_funciona": "Como funciona", "como_funciona_sub": "Do primeiro clique ao dia do passeio, sem complicação.",
        "passo_1": "Escolha o passeio", "passo_1_txt": "Veja o roteiro, o preço e as próximas datas com vaga.",
        "passo_2": "Chame no WhatsApp", "passo_2_txt": "A mensagem já vai pronta, com o passeio, a data e as pessoas.",
        "passo_3": "Confirmamos a reserva", "passo_3_txt": "Nossa equipe confirma a vaga e combina os detalhes com você.",
        "selo_cadastur": "Registrada no CADASTUR", "pronto_para_ir": "Pronto para conhecer a região?",
        "voltar_inicio": "Voltar para o início", "depoimento_original": "",
        "idioma": "Idioma",
        # Mensagem do WhatsApp (vai para a agência, que lê em português)
        "zap_passeio": "Olá! Vi o site e quero reservar *{nome}*.", "zap_geral": "Olá! Vi o site e quero saber mais sobre os passeios.",
        "zap_data": "📅 Data: {d}", "zap_codigo": "Código do site: {c}",
    },
    "en": {
        "passeios": "Tours", "passeios_sub": "Pick a tour, check the dates and talk to us on WhatsApp.",
        "ver_passeios": "See tours", "falar_whatsapp": "Chat on WhatsApp", "fale_conosco": "Contact us",
        "fale_conosco_aria": "Contact us on WhatsApp",
        "por_pessoa": "per person", "por_adulto": "per adult", "crianca": "Child", "gratis": "free",
        "ver_detalhes": "See details and dates →", "em_breve": "Our tours are coming soon. Meanwhile, talk to us on WhatsApp.",
        "por_que": "Why travel with us", "ha_anos": "Welcoming travelers to the region for {n} years.",
        "cadastur": "Registered with CADASTUR (Brazil's Ministry of Tourism) · {n}", "desde": "Since {n}",
        "quem_ja_foi": "What travelers say", "ver_avaliacoes": "See all reviews on {fonte} →",
        "avaliacoes_no": "reviews on", "nota_de": "Rated {nota} out of 5 on {fonte}", "no": "on",
        "busca_titulo": "We pick you up at your hotel",
        "busca_sub": "Pick-up leaves from these points. The exact time comes with your booking confirmation.",
        "busca_antes": "Pick-up about {n} min before departure", "busca_hora": "Pick-up at departure time",
        "busca_outro": "Staying somewhere else? Message us and we'll arrange the pick-up.",
        "perguntas": "Frequently asked questions", "perguntas_passeio": "Questions about this tour",
        "mais_perguntas": "More frequently asked questions →",
        "sobre": "About us", "onde_estamos": "Where we are", "atendimento": "Opening hours",
        "ver_mapa": "View on map →", "pagamento": "Payment methods", "pagamento_curto": "Payment",
        "cancelamento": "Cancellation",
        "todos_passeios": "← All tours", "como_e_o_dia": "What the day looks like",
        "inclui": "What's included", "levar": "What to bring", "bom_saber": "Good to know",
        "melhor_epoca": "Best time to go", "se_chover": "If it rains",
        "esforco": {"leve": "Easy", "moderado": "Moderate effort", "intenso": "Challenging"},
        "a_partir_de": "Ages {n} and up", "todas_idades": "All ages",
        "consulte_valor": "Ask for the price", "data": "Date", "ainda_nao_sei": "Not sure yet",
        "sem_datas": "No open dates in the coming weeks. Message us to hear about the next ones.",
        "adultos": "Adults", "criancas": "Children", "quero": "I want this tour",
        "nota_reserva": "You'll continue on WhatsApp. Our team confirms your booking.",
        "escolher_data": "Choose a date",
        "todos_os_dias": "Every day", "a": "to", "e": "and",
        "guias": "Travel guides", "guias_sub": "What to know before you come.", "ver_guia": "Read the guide →",
        "todos_guias": "← All guides", "guias_relacionados": "Read before you go",
        "passeios_relacionados": "Tours to take",
        "previa": "Preview: this site is not published yet and only you can see it.",
        "nao_encontrada": "Page not found", "nao_encontrada_sub": "The address may have changed.",
        "como_funciona": "How it works", "como_funciona_sub": "From the first click to the day of the tour, hassle-free.",
        "passo_1": "Pick a tour", "passo_1_txt": "See the itinerary, the price and the next dates with seats.",
        "passo_2": "Message us on WhatsApp", "passo_2_txt": "The message is ready to send, with the tour, the date and the group.",
        "passo_3": "We confirm your booking", "passo_3_txt": "Our team confirms your seats and sorts out the details with you.",
        "selo_cadastur": "Registered with CADASTUR", "pronto_para_ir": "Ready to explore the region?",
        "voltar_inicio": "Back to the home page", "depoimento_original": "Review in its original language",
        "idioma": "Language",
        "zap_passeio": "Hi! I saw your website and I'd like to book *{nome}*.", "zap_geral": "Hi! I saw your website and I'd like to know more about your tours.",
        "zap_data": "📅 Date: {d}", "zap_codigo": "Site code: {c}",
    },
    "es": {
        "passeios": "Paseos", "passeios_sub": "Elegí el paseo, mirá las fechas y escribinos por WhatsApp.",
        "ver_passeios": "Ver paseos", "falar_whatsapp": "Escribir por WhatsApp", "fale_conosco": "Contacto",
        "fale_conosco_aria": "Escribinos por WhatsApp",
        "por_pessoa": "por persona", "por_adulto": "por adulto", "crianca": "Niño", "gratis": "gratis",
        "ver_detalhes": "Ver detalles y fechas →", "em_breve": "Pronto, nuestros paseos aquí. Mientras tanto, escribinos por WhatsApp.",
        "por_que": "Por qué viajar con nosotros", "ha_anos": "Hace {n} años recibimos viajeros en la región.",
        "cadastur": "Agencia registrada en el CADASTUR (Ministerio de Turismo de Brasil) · {n}", "desde": "Desde {n}",
        "quem_ja_foi": "Lo que dicen los viajeros", "ver_avaliacoes": "Ver todas las reseñas en {fonte} →",
        "avaliacoes_no": "reseñas en", "nota_de": "Calificación {nota} de 5 en {fonte}", "no": "en",
        "busca_titulo": "Te buscamos en tu alojamiento",
        "busca_sub": "La búsqueda sale de estos puntos. El horario exacto va con la confirmación de la reserva.",
        "busca_antes": "Búsqueda unos {n} min antes de la salida", "busca_hora": "Búsqueda en el horario de salida",
        "busca_outro": "¿Te alojás en otro lugar? Escribinos y coordinamos la búsqueda.",
        "perguntas": "Preguntas frecuentes", "perguntas_passeio": "Preguntas sobre este paseo",
        "mais_perguntas": "Más preguntas frecuentes →",
        "sobre": "Quiénes somos", "onde_estamos": "Dónde estamos", "atendimento": "Horario de atención",
        "ver_mapa": "Ver en el mapa →", "pagamento": "Formas de pago", "pagamento_curto": "Pago",
        "cancelamento": "Cancelación",
        "todos_passeios": "← Todos los paseos", "como_e_o_dia": "Cómo es el día",
        "inclui": "Qué incluye", "levar": "Qué llevar", "bom_saber": "Bueno saber",
        "melhor_epoca": "Mejor época", "se_chover": "Si llueve",
        "esforco": {"leve": "Esfuerzo bajo", "moderado": "Esfuerzo moderado", "intenso": "Esfuerzo alto"},
        "a_partir_de": "Desde los {n} años", "todas_idades": "Todas las edades",
        "consulte_valor": "Consultá el precio", "data": "Fecha", "ainda_nao_sei": "Todavía no sé",
        "sem_datas": "No hay fechas abiertas en las próximas semanas. Escribinos para saber las próximas.",
        "adultos": "Adultos", "criancas": "Niños", "quero": "Quiero este paseo",
        "nota_reserva": "Seguís la conversación por WhatsApp. Nuestro equipo confirma la reserva.",
        "escolher_data": "Elegir fecha",
        "todos_os_dias": "Todos los días", "a": "a", "e": "y",
        "guias": "Guías de la región", "guias_sub": "Lo que hay que saber antes de venir.", "ver_guia": "Leer la guía →",
        "todos_guias": "← Todas las guías", "guias_relacionados": "Para leer antes de ir",
        "passeios_relacionados": "Paseos para hacer",
        "previa": "Vista previa: este sitio todavía no está publicado y solo vos lo estás viendo.",
        "nao_encontrada": "Página no encontrada", "nao_encontrada_sub": "La dirección puede haber cambiado.",
        "como_funciona": "Cómo funciona", "como_funciona_sub": "Del primer clic al día del paseo, sin complicaciones.",
        "passo_1": "Elegí el paseo", "passo_1_txt": "Mirá el itinerario, el precio y las próximas fechas con lugar.",
        "passo_2": "Escribinos por WhatsApp", "passo_2_txt": "El mensaje ya va listo, con el paseo, la fecha y las personas.",
        "passo_3": "Confirmamos la reserva", "passo_3_txt": "Nuestro equipo confirma los lugares y coordina los detalles con vos.",
        "selo_cadastur": "Registrada en CADASTUR", "pronto_para_ir": "¿Listo para conocer la región?",
        "voltar_inicio": "Volver al inicio", "depoimento_original": "Reseña en su idioma original",
        "idioma": "Idioma",
        "zap_passeio": "¡Hola! Vi el sitio y quiero reservar *{nome}*.", "zap_geral": "¡Hola! Vi el sitio y quiero saber más sobre los paseos.",
        "zap_data": "📅 Fecha: {d}", "zap_codigo": "Código del sitio: {c}",
    },
}


def valido(idioma: str | None) -> str:
    return idioma if idioma in IDIOMAS else PADRAO


def dia_rotulo(dia_iso: str, idioma: str) -> str:
    """"sex, 16/10" / "Fri, Oct 16" / "vie 16/10": o rótulo do dia no seletor."""
    d = date.fromisoformat(dia_iso[:10])
    idioma = valido(idioma)
    curto = DIAS_CURTOS[idioma][d.weekday()]
    if idioma == "en":
        return f"{curto}, {MESES_CURTOS['en'][d.month - 1]} {d.day}"
    return f"{curto} {d.day:02d}/{d.month:02d}"


def quando_sai(grade: list, idioma: str) -> str | None:
    """"Seg a sáb · 08:00 e 14:00", o resumo da grade de saídas, no idioma da página."""
    idioma = valido(idioma)
    t = T[idioma]
    dias = [i for i, d in enumerate(grade or []) if d]
    horas = sorted({s["hora"] for d in grade or [] for s in (d or [])})
    if not dias:
        return None
    nomes = DIAS_CURTOS[idioma]
    if dias == list(range(7)):
        txt_dias = t["todos_os_dias"]
    elif dias == list(range(dias[0], dias[-1] + 1)) and len(dias) > 2:
        txt_dias = f"{nomes[dias[0]]} {t['a']} {nomes[dias[-1]]}"
    else:
        txt_dias = ", ".join(nomes[i] for i in dias)
    txt_dias = txt_dias[:1].upper() + txt_dias[1:]
    txt_horas = horas[0] if len(horas) == 1 else ", ".join(horas[:-1]) + f" {t['e']} {horas[-1]}"
    return f"{txt_dias} · {txt_horas}"

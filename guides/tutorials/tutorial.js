(() => {
  const film = document.querySelector(".comet-film");
  if (film) {
    const button = document.querySelector(".film-toggle");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const label = () => { button.textContent = film.paused ? "Play motion" : "Pause motion"; };
    const play = () => film.play().catch(label);
    button.hidden = false;
    button.addEventListener("click", () => film.paused ? play() : film.pause());
    film.addEventListener("play", label);
    film.addEventListener("pause", label);
    motion.addEventListener("change", () => motion.matches ? film.pause() : play());
    if (!motion.matches) play();
  }

  const lessons = [...document.querySelectorAll(".lesson")];
  if (!lessons.length) return;
  const railLinks = document.querySelectorAll(".rail .step-group a");
  const menu = document.querySelector(".step-menu");
  const wide = window.matchMedia("(min-width: 52.01rem)");
  const originalTitle = document.title;
  document.body.classList.add("is-reading");
  menu.open = wide.matches;
  wide.addEventListener("change", () => { menu.open = wide.matches; });

  function showLocation(moveFocus) {
    const id = location.hash.slice(1);
    const directory = id === "tasks";
    const active = lessons.find(lesson => lesson.id === id) ||
      (directory && lessons.find(lesson => !lesson.hidden)) || lessons[0];
    for (const lesson of lessons) lesson.hidden = lesson !== active;
    for (const link of railLinks) {
      if (link.hash === "#" + active.id) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    }
    menu.open = directory || wide.matches;
    const heading = directory ? menu.querySelector("summary") : active.querySelector("h2");
    document.title = active.querySelector("h2").textContent + " | " + originalTitle;
    if (moveFocus || directory) {
      heading.focus({ preventScroll: true });
      heading.scrollIntoView({ block:"start" });
    }
  }

  showLocation(false);
  window.addEventListener("hashchange", () => showLocation(true));
  document.querySelector(".skip").addEventListener("click", event => {
    event.preventDefault();
    document.querySelector(".lesson:not([hidden]) h2").focus();
  });

  for (const controls of document.querySelectorAll(".frame-switch")) {
    const images = controls.parentElement.querySelectorAll(".enlarge");
    const buttons = controls.querySelectorAll("button");
    function selectFrame(index) {
      buttons.forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
      images.forEach((link, i) => { link.hidden = i !== index; });
    }
    controls.hidden = false;
    buttons.forEach((button, i) => button.addEventListener("click", () => selectFrame(i)));
    selectFrame(0);
  }

  const viewer = document.querySelector("dialog");
  if (typeof viewer.showModal !== "function") return;
  const fullImage = viewer.querySelector("img");
  const scale = viewer.querySelector(".scale");
  for (const link of document.querySelectorAll(".enlarge")) {
    link.addEventListener("click", event => {
      if (event.ctrlKey || event.shiftKey || event.altKey || event.metaKey) return;
      event.preventDefault();
      fullImage.src = link.href;
      fullImage.alt = link.querySelector("img").alt;
      viewer.querySelector("#viewer-title").textContent = link.closest("article").querySelector("h2").textContent;
      viewer.classList.remove("actual-size");
      scale.setAttribute("aria-pressed", "false");
      viewer.showModal();
    });
  }
  scale.addEventListener("click", () => {
    scale.setAttribute("aria-pressed", String(viewer.classList.toggle("actual-size")));
  });
  viewer.querySelector(".close-viewer").addEventListener("click", () => viewer.close());
  viewer.addEventListener("click", event => {
    if (event.target === viewer) {
      const rect = viewer.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right ||
          event.clientY < rect.top || event.clientY > rect.bottom) viewer.close();
    }
  });
})();

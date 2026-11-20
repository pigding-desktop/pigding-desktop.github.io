"use strict";
const pet = document.querySelector(".pet");
const image = document.getElementById("pet-image");
const reaction = document.getElementById("reaction");
const options = [...document.querySelectorAll("[data-skin]")];
const moods = [
  ["happy", "开心 · 最喜欢你！"], ["show", "展示 · 看我的！"],
  ["relax", "放松 · 陪你歇一会儿"], ["anger", "生气 · 哼！"],
  ["shock", "惊讶 · 吓我一跳！"], ["fear", "害怕 · 抱抱我"],
  ["sad", "难过 · 要抱抱"], ["alert", "警戒 · 谁在那里？"]
];
let skin = "demon", mood = "happy", previous = "happy", bag = [];
function outfit() { return skin === "demon" ? "普通色恶魔叮" : "异色恶魔叮"; }
function paint() { image.src = `assets/${skin}-${mood}.webp`; image.alt = outfit(); }
function refill() {
  bag = moods.slice();
  for (let i = bag.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [bag[i], bag[j]] = [bag[j], bag[i]];
  }
  if (bag[0][0] === previous) [bag[0], bag[1]] = [bag[1], bag[0]];
}
pet.addEventListener("click", () => {
  if (!bag.length) refill();
  const [id, text] = bag.shift();
  previous = mood = id;
  paint(); reaction.textContent = text;
  pet.classList.remove("bump"); void pet.offsetWidth; pet.classList.add("bump");
});
pet.addEventListener("animationend", () => pet.classList.remove("bump"));
options.forEach(option => option.addEventListener("click", () => {
  skin = option.dataset.skin; mood = previous = "happy"; bag = [];
  options.forEach(button => button.setAttribute("aria-pressed", String(button === option)));
  paint(); reaction.textContent = `已切换为${outfit()} · 点一下试试`;
}));

<template>
  <div class="app-container">
    <DynamicGlobe />
  </div>
</template>

<script setup>
import DynamicGlobe from './components/DynamicGlobe.vue'
</script>

<style>
.app-container {
  background-color: #0a0a1a;
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  margin: 0;
  padding: 0;
  font-family: Arial, sans-serif;
}
</style>
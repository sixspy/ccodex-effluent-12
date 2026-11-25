export const releaseViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>准排放记录</h3>
      <table>
        <thead><tr><th>准排号</th><th>采样</th><th>口</th><th>状态</th><th>污泥背书</th><th>值班</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td>{{ r.release_ref }}</td><td>{{ r.sample_code }}</td><td>{{ r.outfall_code }}</td>
            <td :class="r.status==='已准排' ? 'pass' : (r.status==='影子意图' ? 'warn' : 'muted')">{{ r.status }}</td>
            <td :class="r.sludge_backed ? 'pass' : 'fail'">{{ r.sludge_backed ? "有排程" : "空桩" }}</td>
            <td>{{ r.officer }}</td><td>{{ r.released_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>签发准排</h3>
      <p class="muted">口径：无污泥排程（空桩无消化仓行）禁准排，写口原子拒收并记污泥禁准裁决；可先登记影子意图，待仓位计划升正。</p>
      <label>已裁定采样</label>
      <select v-model.number="releaseForm.sample_id">
        <option v-for="s in releasableSamples" :key="s.id" :value="s.id">{{ s.sample_code }} · {{ s.status }} · {{ sludgeJobs.some(j => j.status === '排队' && j.plant_id === ((outfalls.find(o => o.id === s.outfall_id) || {}).plant_id)) ? '污泥有排程' : '污泥空桩·禁准' }}</option>
      </select>
      <label>值班员</label><input v-model="releaseForm.officer"/>
      <div class="actions"><button @click="createRelease">准排</button></div>
    </div>
  </div>`;

import { createRouter, createWebHashHistory } from "vue-router";

export const routes = [
    {
        path: "/",
        redirect: "dm",
        meta: {
            hideInMenu: true,
        },
    },
    {
        path: "/dm",
        name: "dm",
        component: () => import("../views/dm.vue"),
        meta: {
            name: "大麦",
        },
    },
];

export default createRouter({
    history: createWebHashHistory(),
    routes,
});

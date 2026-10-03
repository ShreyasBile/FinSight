import { Icons } from "../icons/Icons.jsx";

export const NAV = {
  user: [
    {group:"Applications",items:[
      {id:"new-application", label:"Apply for Loan", icon:Icons.plus},
      {id:"my-applications", label:"My Applications",icon:Icons.apps},
      {id:"notifications",   label:"Notifications",  icon:Icons.bell,badgeColor:"red"},
    ]},
  ],
  officer: [
    {group:"Applications",items:[
      {id:"new-app",      label:"New Application",   icon:Icons.plus},
      {id:"applications", label:"My Applications",   icon:Icons.apps,badgeColor:"amber"},
      {id:"app-detail",   label:"Application Detail", icon:Icons.eye},
      {id:"customer360",  label:"Customer 360°",     icon:Icons.users},
    ]},
    {group:"Intelligence",items:[
      {id:"fraud",        label:"Fraud Detection",   icon:Icons.fraud,badgeColor:"red"},
      {id:"policy",       label:"Policy Engine",     icon:Icons.policy},
    ]},
    {group:"Outputs",items:[
      {id:"reports",      label:"My Reports",        icon:Icons.report},
      {id:"notifications",label:"Notifications",     icon:Icons.bell,},
      {id:"settings",     label:"Settings",          icon:Icons.settings},
    ]},
  ],
  head: [
    {group:"Overview",items:[
      {id:"dashboard",    label:"Dashboard",         icon:Icons.dashboard},
      {id:"applications", label:"All Applications",  icon:Icons.apps,},
      {id:"app-detail",   label:"Application Detail", icon:Icons.eye},
      {id:"customer360",  label:"Customer 360°",     icon:Icons.users},
    ]},
    {group:"Intelligence",items:[
      {id:"risk",         label:"Portfolio Risk",    icon:Icons.risk},
      {id:"fraud",        label:"Fraud Overview",    icon:Icons.fraud,badgeColor:"red"},
      {id:"policy",       label:"Policy Engine",     icon:Icons.policy},
      {id:"compliance",   label:"RBI Compliance",    icon:Icons.compliance},
      {id:"graph",        label:"Knowledge Graph",   icon:Icons.graph},
      {id:"agents",       label:"Agent Activity",    icon:Icons.cpu},
    ]},
    {group:"Outputs",items:[
      {id:"reports",      label:"Reports & Memos",   icon:Icons.report},
      {id:"notifications",label:"Notifications",     icon:Icons.bell,badgeColor:"red"},
      {id:"settings",     label:"System Settings",   icon:Icons.settings},
    ]},
  ],
};

export const DEFAULT_PAGE = { user:"new-application", officer:"new-app", head:"dashboard" };

export const TITLES = {
  user:{
    "new-application":"Apply for a Loan","new-app":"New Loan Application",
    "my-applications":"My Applications","app-detail":"Application Detail",
    "notifications":"Notifications",
  },
  officer:{
    "new-app":"New Loan Application","applications":"My Applications","app-detail":"Application Detail",
    "customer360":"Customer 360°","risk":"Portfolio Risk","graph":"Knowledge Graph",
    "fraud":"Fraud Detection","policy":"Policy Engine",
    "reports":"My Reports","notifications":"Notifications","settings":"My Settings",
  },
  head:{
    "dashboard":"Dashboard","applications":"All Applications","app-detail":"Application Detail",
    "customer360":"Customer 360°","risk":"Portfolio Risk","fraud":"Fraud — All Branches",
    "policy":"Policy Engine","compliance":"RBI Compliance",
    "graph":"Knowledge Graph","reports":"Reports & Memos","agents":"Agent Activity",
    "notifications":"Notifications","settings":"System Settings",
  },
};
